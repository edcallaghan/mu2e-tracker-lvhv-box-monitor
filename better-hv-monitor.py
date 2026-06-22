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

def timeseries(supplies, channels, cmd, label, interval, fig, ax, xlim, ylim, yscale, label_axes, logger):
    expire = xlim[1]
    buffs = {
        k: ClockedBuffer(expiration=datetime.timedelta(seconds=expire),
                         interval=60)
            for k in channels
    }

    poll_all_queries(supplies, cmd, channels, buffs, interval)

    if label_axes:
        ax.set_xlabel('Time ago [s]')
        ax.set_ylabel(label)
    lines = {}
    for channel in channels:
        label = 'Channel %d' % channel
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

def update_subplot(frame, ax, channels, lines, buffs):
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

    for k in channels:
        if k in latest_values:
            lines[k].set_label(f'{k}: {latest_values[k]:.1f}')
        else:
            lines[k].set_label(f'Channel {k}')

    rv = ax.legend(loc='best', ncols=4, fontsize=8)
    return lines.values()

def update(frame, axs, channels, lines, buffs):
    rv = []
    for ax, subchannels, sublines, subbuffs in zip(axs, channels, lines, buffs):
        updated = update_subplot(frame, ax, subchannels, sublines, subbuffs)
        rv += updated
    return rv

def main(args):
    fig = plt.figure()
    axss = fig.subplots(nrows=len(args.ports), ncols=3)
    axs = []
    channels = []
    lines = []
    buffs = []

    for i,port in enumerate(args.ports):
        mksupply = lambda: PowerSupplyServerConnection(args.host, port,
                                                       header=args.header)
        mksupplies = lambda chs: [mksupply() for ch in chs]
        this_channels = args.channels

        if 1 < len(args.ports):
            row = axss[i]
        else:
            row = axss
        axs += [a for a in row]

        label_axes = False
        if i == len(args.ports) - 1:
            label_axes = True

        row[0].set_title('Port %s' % str(port))

        c, l, b = timeseries(mksupplies(this_channels), this_channels,
                              'get_vhv', 'Voltage [V]', 1.0,
                              fig, row[0],
                              (0.0, 300.0), (0.0, 3000.0),
                              'linear',
                              label_axes,
                              lambda *args: None,
                             )
        channels.append(c)
        lines.append(l)
        buffs.append(b)

        c, l, b = timeseries(mksupplies(this_channels), this_channels,
                              'get_ihv', 'Current [uA]', 0.1,
                              fig, row[1],
                              (0.0, 300.0), (0.0, 20.0),
                              'linear',
                              label_axes,
                              lambda *args: None,
                             )
        channels.append(c)
        lines.append(l)
        buffs.append(b)

        c, l, b = timeseries(mksupplies(this_channels), this_channels,
                              'pcb_temp', 'Temperature [degC]', 10.0,
                              fig, row[2],
                              (0.0, 300.0), (5.0, 50.0),
                              'linear',
                              label_axes,
                              lambda *args: None,
                             )

        channels.append(c)
        lines.append(l)
        buffs.append(b)

    curried = partial(update,
                      axs=axs, channels=channels, lines=lines, buffs=buffs)
    animation = FuncAnimation(fig, curried,
                              frames=forever,
                              init_func=init,
                              repeat=False,
                              interval=500,
                              blit=True)
    plt.tight_layout(pad=0.0, w_pad=-2.0, h_pad=-0.5)
    plt.show()

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', type=str, dest='host', default='localhost')
    parser.add_argument('--ports', type=int, dest='ports', nargs='+', required=True)
    parser.add_argument('--header', type=str, dest='header', required=True)
    parser.add_argument('-c', type=int, dest='channels', nargs='+', default=[])
    
    args = parser.parse_args()
    main(args)
