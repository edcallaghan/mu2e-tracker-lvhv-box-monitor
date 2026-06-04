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
    def __init__(self, expiration):
        self.expiration = expiration
        self.lock = threading.Lock()

    def Consume(self, item):
        wrapped = (item, now())
        self.lock.acquire()
        self.append(wrapped)
        self.lock.release()
        self.Resolve()

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

def timeseries(supplies, channels, cmd, label, xlim, ylim, yscale, logger):
    expire = xlim[1]
    buffs = {
        k: ClockedBuffer(expiration=datetime.timedelta(seconds=expire))
            for k in channels
    }

    fig = plt.figure()
    plt.xlabel('Time ago [s]')
    plt.ylabel(label)
    ax = plt.gca()
    lines = {}
    for channel in channels:
        label = 'Channel %d' % channel
        lines[channel], *rest = ax.plot([], [], '-', label=label)

    legend = None

    def init():
        nonlocal legend
        ax.set_xlim(*xlim)
        ax.set_ylim(*ylim)
        ax.set_yscale(yscale)
        #legend = ax.legend(ncols=3)
        ax.invert_xaxis()
        return list(lines.values())

    def update(frame, lines, buffs):
        nonlocal legend
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
                lines[k].set_label(f'{latest_values[k]:.3f}\nChannel {k}')
            else:
                lines[k].set_label(f'Channel {k}')

        if legend is not None:
            legend.remove()
        #legend = ax.legend(ncols=3)
        return lines.values()

    poll_all_queries(supplies, cmd, channels, buffs, 0.1)

    animation = FuncAnimation(fig, partial(update, lines=lines, buffs=buffs),
                              frames=forever,
                              init_func=init,
                              repeat=False,
                              interval=1000,
                              blit=False)
    return animation

def main(args):
    mksupply = lambda: PowerSupplyServerConnection(args.host, args.port,
                                                   header=args.header)
    mksupplies = lambda chs: [mksupply() for ch in chs]
    channels = args.channels

    voltages = timeseries(mksupplies(channels), channels,
                          'get_vhv', 'Voltage [V]',
#                         (0.0, 300.0), (0.0, 3000.0),
                          (0.0, 300.0), (-10.0, 10.0),
                          'linear',
                          lambda *args: None,
                         )
    plt.show()

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', type=str, dest='host', default='localhost')
    parser.add_argument('--port', type=int, dest='port', default=12000)
    parser.add_argument('--header', type=str, dest='header', required=True)
    parser.add_argument('-c', type=int, dest='channels', nargs='+', default=[])
    
    args = parser.parse_args()
    main(args)
