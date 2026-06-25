# Ed Callaghan
# Realtime plots of hv currents and voltages
# Jun 2026

import argparse
from collections import deque
import datetime
from functools import partial
import json
from matplotlib import pyplot as plt
from matplotlib.animation import FuncAnimation
import numpy as np
import os.path
import threading
from time import sleep

from PowerSupplyServerConnection import PowerSupplyServerConnection
from ThreadSafeDict import ThreadSafeDict

def now():
    rv = datetime.datetime.now()
    return rv

class ClockedBuffer(deque):
    def __init__(self, expiration, interval):
        self.expiration = expiration
        self.interval = interval
        self.lock = threading.Lock()

        target = lambda: self.poll_resolve()
        thread = threading.Thread(daemon=True, target=target, args=())
        thread.start()

    def Consume(self, item):
        wrapped = (item, now())
        self.lock.acquire()
        self.append(wrapped)
        self.lock.release()
        #self.Resolve()

    def Resolve(self):
        rn = now()
        self.lock.acquire()
        while 0 < len(self) and (self.expiration < (rn - self[0][1])):
            self.popleft()
        self.lock.release()

    def Snapshot(self):
        self.lock.acquire()
        rv = [item for item in self]
        self.lock.release()
        return rv

    def poll_resolve(self):
        while True:
            sleep(self.interval)
            self.Resolve()

def query_and_set(supply, cmd, channel, out):
    rv = supply.WriteRead(cmd, channel)
    rv = rv[0][0]
    out.Assign(channel, rv)

def threaded_queries(supplies, cmd, channels, out):
    threads = []
    for supply,channel in zip(supplies,channels):
        thread = threading.Thread(name='Channel %d' % channel,
                                  daemon=True,
                                  target=query_and_set,
                                  args=(supply,cmd,channel,out))
        threads.append(thread)

    for thread in threads:
        thread.start()

    while 0 < len(threads):
        for thread in threads:
            thread.join(timeout=1e-6)
            if not thread.is_alive():
                threads.remove(thread)

def serial_queries(supplies, cmd, channels, out):
    threads = []
    for supply,channel in zip(supplies,channels):
        query_and_set(supply, cmd, channel, out)
        sleep(0.02)

def poll_queries(supplies, cmd, channels, buffs, interval):
    while True:
        rv = ThreadSafeDict()
        threaded_queries(supplies, cmd, channels, rv)
        rv = rv.AsDict()
        for k,v in rv.items():
            buffs[k].Consume(v)
        sleep(interval)

def poll_all_queries(supplies, cmd, channels, buffs, interval):
    threads = []
    for supply,channel in zip(supplies,channels):
        thread = threading.Thread(daemon=True,
                                  target=poll_queries,
                                  args=([supply], cmd, [channel], buffs, interval)
                                )
        threads.append(thread)

    for thread in threads:
        thread.start()

def forever():
    while True:
        yield None

def timeseries(supplies, channels, cmd, label, interval, fig, ax, xlim, ylim, yscale, label_axes, logger, resolve_interval=60, fonts=None, per_channel=True):
    expire = xlim[1]
    buffs = {
        k: ClockedBuffer(expiration=datetime.timedelta(seconds=expire),
                         interval=resolve_interval)
            for k in channels
    }

    poll_all_queries(supplies, cmd, channels, buffs, interval)

    if label_axes:
        ax.set_xlabel('Time ago [s]', fontsize=fonts.get('axis_label', 10) if fonts else 10)
        ax.set_ylabel(label, fontsize=fonts.get('axis_label', 10) if fonts else 10)
    if fonts:
        ax.tick_params(axis='both', which='major', labelsize=fonts.get('tick', 8))
    lines = {}
    for channel in channels:
        label = 'Channel %d' % channel if per_channel else 'Global'
        lines[channel], *rest = ax.plot([], [], '-', label=label)
    ax.set_xlim(*xlim)
    ax.set_ylim(*ylim)
    ax.set_yscale(yscale)
    ax.invert_xaxis()

    global init_tups
    tup = lines.values()
    init_tups.append(tup)

    return channels, lines, buffs

init_tups = []
def init():
    global init_tups
    rv = []
    for tup in init_tups:
        rv += tup
    return rv

def update_subplot(frame, ax, channels, lines, buffs, legend, fonts=None):
    rn = now()
    latest_values = {}
    for k in lines.keys():
        buff = buffs[k]
        snapshot = buff.Snapshot()
        xx = [(rn - pair[1]).total_seconds() for pair in snapshot]
        yy = [pair[0] for pair in snapshot]
        lines[k].set_data(xx, yy)
        if 0 < len(yy):
            latest_values[k] = yy[-1]

    texts = legend.get_texts()
    for i,k in enumerate(channels):
        if k in latest_values:
            lines[k].set_label(f'{k}: {latest_values[k]:.1f}')
        else:
            lines[k].set_label(f'Channel {k}')
        texts[i].set_text(lines[k].get_label())

    rv = list(lines.values()) + [legend]
    return rv

def update(frame, axs, channels, lines, buffs, legends, fonts=None):
    rv = []
    for ax, subchannels, sublines, subbuffs, legend in zip(axs, channels, lines, buffs, legends):
        updated = update_subplot(frame, ax, subchannels, sublines, subbuffs, legend, fonts=fonts)
        rv += updated
    return rv

def main(args):
    with open(args.config, 'r') as f:
        config = json.load(f)

    supplies = config['supplies']
    metrics = config['metrics']
    channels_cfg = config.get('channels', [])
    animation_interval = config.get('animation_interval', 500)
    fonts_cfg = config.get('fonts', {})

    fig = plt.figure()
    axss = fig.subplots(nrows=len(supplies), ncols=len(metrics), squeeze=False)
    axs = []
    channels = []
    lines = []
    buffs = []
    legends = []

    for i, supply_cfg in enumerate(supplies):
        host = supply_cfg['host']
        port = supply_cfg['port']

        mksupply = lambda: PowerSupplyServerConnection(host, port, header=args.header)

        label_axes = (i == len(supplies) - 1)

        for j, metric in enumerate(metrics):
            ax = axss[i, j]
            if j == 0:
                ax.set_title(supply_cfg.get('label', 'Port %s' % str(port)), fontsize=fonts_cfg.get('title', 12))

            this_channels = channels_cfg if metric.get('per_channel', True) else [0]
            c, l, b = timeseries(
                [mksupply() for _ in this_channels],
                this_channels,
                metric['cmd'],
                metric['label'],
                metric['polling_interval'],
                fig, ax,
                tuple(metric['xlim']),
                tuple(metric['ylim']),
                metric['yscale'],
                label_axes,
                lambda *args: None,
                resolve_interval=config.get('buffer_resolve_interval', 60),
                fonts=fonts_cfg,
                per_channel=metric.get('per_channel', True)
            )
            axs.append(ax)
            channels.append(c)
            lines.append(l)
            buffs.append(b)

            fonts = config['fonts']
            legend = ax.legend(loc='upper left', bbox_to_anchor=(0,1), ncols=3, fontsize=fonts.get('legend', 8) if fonts else 8)
            legends.append(legend)

    curried = partial(update,
                      axs=axs, channels=channels, lines=lines, buffs=buffs, legends=legends, fonts=fonts_cfg)
    animation = FuncAnimation(fig, curried,
                                frames=forever,
                                init_func=init,
                                repeat=False,
                                interval=animation_interval,
                                blit=True)
    plt.subplots_adjust(**config.get('margins', {}))
    plt.show()

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=str, dest='config', default='config.json', help='Path to config JSON file')
    parser.add_argument('--header', type=str, dest='header', required=True)

    args = parser.parse_args()
    main(args)

