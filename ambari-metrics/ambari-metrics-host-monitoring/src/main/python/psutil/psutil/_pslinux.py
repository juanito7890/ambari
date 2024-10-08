#!/usr/bin/env python3

# Copyright (c) 2009, Giampaolo Rodola'. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Linux platform implementation."""

from __future__ import division

import base64
import errno
import os
import re
import socket
import struct
import sys
import warnings

from psutil import _common
from psutil import _psposix
from psutil._common import (isfile_strict, usage_percent, deprecated)
from psutil._compat import PY3, xrange, namedtuple, wraps, b, defaultdict
import _psutil_linux as cext
import _psutil_posix


__extra__all__ = [
    # io prio constants
    "IOPRIO_CLASS_NONE", "IOPRIO_CLASS_RT", "IOPRIO_CLASS_BE",
    "IOPRIO_CLASS_IDLE",
    # connection status constants
    "CONN_ESTABLISHED", "CONN_SYN_SENT", "CONN_SYN_RECV", "CONN_FIN_WAIT1",
    "CONN_FIN_WAIT2", "CONN_TIME_WAIT", "CONN_CLOSE", "CONN_CLOSE_WAIT",
    "CONN_LAST_ACK", "CONN_LISTEN", "CONN_CLOSING",
    # other
    "phymem_buffers", "cached_phymem"]


# --- constants

HAS_PRLIMIT = hasattr(cext, "linux_prlimit")

# RLIMIT_* constants, not guaranteed to be present on all kernels
if HAS_PRLIMIT:
    for name in dir(cext):
        if name.startswith('RLIM'):
            __extra__all__.append(name)

# Number of clock ticks per second
CLOCK_TICKS = os.sysconf("SC_CLK_TCK")
PAGESIZE = os.sysconf("SC_PAGE_SIZE")
BOOT_TIME = None  # set later
DEFAULT_ENCODING = sys.getdefaultencoding()

# ioprio_* constants http://linux.die.net/man/2/ioprio_get
IOPRIO_CLASS_NONE = 0
IOPRIO_CLASS_RT = 1
IOPRIO_CLASS_BE = 2
IOPRIO_CLASS_IDLE = 3

# taken from /fs/proc/array.c
PROC_STATUSES = {
    "R": _common.STATUS_RUNNING,
    "S": _common.STATUS_SLEEPING,
    "D": _common.STATUS_DISK_SLEEP,
    "T": _common.STATUS_STOPPED,
    "t": _common.STATUS_TRACING_STOP,
    "Z": _common.STATUS_ZOMBIE,
    "X": _common.STATUS_DEAD,
    "x": _common.STATUS_DEAD,
    "K": _common.STATUS_WAKE_KILL,
    "W": _common.STATUS_WAKING
}

# http://students.mimuw.edu.pl/lxr/source/include/net/tcp_states.h
TCP_STATUSES = {
    "01": _common.CONN_ESTABLISHED,
    "02": _common.CONN_SYN_SENT,
    "03": _common.CONN_SYN_RECV,
    "04": _common.CONN_FIN_WAIT1,
    "05": _common.CONN_FIN_WAIT2,
    "06": _common.CONN_TIME_WAIT,
    "07": _common.CONN_CLOSE,
    "08": _common.CONN_CLOSE_WAIT,
    "09": _common.CONN_LAST_ACK,
    "0A": _common.CONN_LISTEN,
    "0B": _common.CONN_CLOSING
}

# set later from __init__.py
NoSuchProcess = None
AccessDenied = None
TimeoutExpired = None


# --- named tuples

def _get_cputimes_fields():
    """Return a namedtuple of variable fields depending on the
    CPU times available on this Linux kernel version which may be:
    (user, nice, system, idle, iowait, irq, softirq, [steal, [guest,
     [guest_nice]]])
    """
    f = open('/proc/stat', 'rb')
    try:
        values = f.readline().split()[1:]
    finally:
        f.close()
    fields = ['user', 'nice', 'system', 'idle', 'iowait', 'irq', 'softirq']
    vlen = len(values)
    if vlen >= 8:
        # Linux >= 2.6.11
        fields.append('steal')
    if vlen >= 9:
        # Linux >= 2.6.24
        fields.append('guest')
    if vlen >= 10:
        # Linux >= 3.2.0
        fields.append('guest_nice')
    return fields


scputimes = namedtuple('scputimes', _get_cputimes_fields())

svmem = namedtuple(
    'svmem', ['total', 'available', 'percent', 'used', 'free',
              'active', 'inactive', 'buffers', 'cached'])

pextmem = namedtuple('pextmem', 'rss vms shared text lib data dirty')

pmmap_grouped = namedtuple(
    'pmmap_grouped', ['path', 'rss', 'size', 'pss', 'shared_clean',
                      'shared_dirty', 'private_clean', 'private_dirty',
                      'referenced', 'anonymous', 'swap'])

pmmap_ext = namedtuple(
    'pmmap_ext', 'addr perms ' + ' '.join(pmmap_grouped._fields))


# --- system memory

def virtual_memory():
    total, free, buffers, shared, _, _ = cext.linux_sysinfo()
    cached = active = inactive = None
    f = open('/proc/meminfo', 'rb')
    CACHED, ACTIVE, INACTIVE = b("Cached:"), b("Active:"), b("Inactive:")
    try:
        for line in f:
            if line.startswith(CACHED):
                cached = int(line.split()[1]) * 1024
            elif line.startswith(ACTIVE):
                active = int(line.split()[1]) * 1024
            elif line.startswith(INACTIVE):
                inactive = int(line.split()[1]) * 1024
            if (cached is not None
                    and active is not None
                    and inactive is not None):
                break
        else:
            # we might get here when dealing with exotic Linux flavors, see:
            # http://code.google.com/p/psutil/issues/detail?id=313
            msg = "'cached', 'active' and 'inactive' memory stats couldn't " \
                  "be determined and were set to 0"
            warnings.warn(msg, RuntimeWarning)
            cached = active = inactive = 0
    finally:
        f.close()
    avail = free + buffers + cached
    used = total - free
    percent = usage_percent((total - avail), total, _round=1)
    return svmem(total, avail, percent, used, free,
                 active, inactive, buffers, cached)


def swap_memory():
    _, _, _, _, total, free = cext.linux_sysinfo()
    used = total - free
    percent = usage_percent(used, total, _round=1)
    # get pgin/pgouts
    f = open("/proc/vmstat", "rb")
    SIN, SOUT = b('pswpin'), b('pswpout')
    sin = sout = None
    try:
        for line in f:
            # values are expressed in 4 kilo bytes, we want bytes instead
            if line.startswith(SIN):
                sin = int(line.split(b(' '))[1]) * 4 * 1024
            elif line.startswith(SOUT):
                sout = int(line.split(b(' '))[1]) * 4 * 1024
            if sin is not None and sout is not None:
                break
        else:
            # we might get here when dealing with exotic Linux flavors, see:
            # http://code.google.com/p/psutil/issues/detail?id=313
            msg = "'sin' and 'sout' swap memory stats couldn't " \
                  "be determined and were set to 0"
            warnings.warn(msg, RuntimeWarning)
            sin = sout = 0
    finally:
        f.close()
    return _common.sswap(total, used, free, percent, sin, sout)


@deprecated(replacement='psutil.virtual_memory().cached')
def cached_phymem():
    return virtual_memory().cached


@deprecated(replacement='psutil.virtual_memory().buffers')
def phymem_buffers():
    return virtual_memory().buffers


# --- CPUs

def cpu_times():
    """Return a named tuple representing the following system-wide
    CPU times:
    (user, nice, system, idle, iowait, irq, softirq [steal, [guest,
     [guest_nice]]])
    Last 3 fields may not be available on all Linux kernel versions.
    """
    f = open('/proc/stat', 'rb')
    try:
        values = f.readline().split()
    finally:
        f.close()
    fields = values[1:len(scputimes._fields) + 1]
    fields = [float(x) / CLOCK_TICKS for x in fields]
    return scputimes(*fields)


def per_cpu_times():
    """Return a list of namedtuple representing the CPU times
    for every CPU available on the system.
    """
    cpus = []
    f = open('/proc/stat', 'rb')
    try:
        # get rid of the first line which refers to system wide CPU stats
        f.readline()
        CPU = b('cpu')
        for line in f:
            if line.startswith(CPU):
                values = line.split()
                fields = values[1:len(scputimes._fields) + 1]
                fields = [float(x) / CLOCK_TICKS for x in fields]
                entry = scputimes(*fields)
                cpus.append(entry)
        return cpus
    finally:
        f.close()


def cpu_count_logical():
    """Return the number of logical CPUs in the system."""
    try:
        return os.sysconf("SC_NPROCESSORS_ONLN")
    except ValueError:
        # as a second fallback we try to parse /proc/cpuinfo
        num = 0
        f = open('/proc/cpuinfo', 'rb')
        try:
            lines = f.readlines()
        finally:
            f.close()
        PROCESSOR = b('processor')
        for line in lines:
            if line.lower().startswith(PROCESSOR):
                num += 1

    # unknown format (e.g. amrel/sparc architectures), see:
    # http://code.google.com/p/psutil/issues/detail?id=200
    # try to parse /proc/stat as a last resort
    if num == 0:
        f = open('/proc/stat', 'rt')
        try:
            lines = f.readlines()
        finally:
            f.close()
        search = re.compile('cpu\d')
        for line in lines:
            line = line.split(' ')[0]
            if search.match(line):
                num += 1

    if num == 0:
        # mimic os.cpu_count()
        return None
    return num


def cpu_count_physical():
    """Return the number of physical CPUs in the system."""
    f = open('/proc/cpuinfo', 'rb')
    try:
        lines = f.readlines()
    finally:
        f.close()
    found = set()
    PHYSICAL_ID = b('physical id')
    for line in lines:
        if line.lower().startswith(PHYSICAL_ID):
            found.add(line.strip())
    if found:
        return len(found)
    else:
        return None  # mimic os.cpu_count()


# --- other system functions

def users():
    """Return currently connected users as a list of namedtuples."""
    retlist = []
    rawlist = cext.users()
    for item in rawlist:
        user, tty, hostname, tstamp, user_process = item
        # note: the underlying C function includes entries about
        # system boot, run level and others.  We might want
        # to use them in the future.
        if not user_process:
            continue
        if hostname == ':0.0':
            hostname = 'localhost'
        nt = _common.suser(user, tty or None, hostname, tstamp)
        retlist.append(nt)
    return retlist


def boot_time():
    """Return the system boot time expressed in seconds since the epoch."""
    global BOOT_TIME
    f = open('/proc/stat', 'rb')
    try:
        BTIME = b('btime')
        for line in f:
            if line.startswith(BTIME):
                ret = float(line.strip().split()[1])
                BOOT_TIME = ret
                return ret
        raise RuntimeError("line 'btime' not found")
    finally:
        f.close()


# --- processes

def pids():
    """Returns a list of PIDs currently running on the system."""
    return [int(x) for x in os.listdir(b('/proc')) if x.isdigit()]


def pid_exists(pid):
    """Check For the existence of a unix pid."""
    return _psposix.pid_exists(pid)


# --- network

class Connections:
    """A wrapper on top of /proc/net/* files, retrieving per-process
    and system-wide open connections (TCP, UDP, UNIX) similarly to
    "netstat -an".

    Note: in case of UNIX sockets we're only able to determine the
    local endpoint/path, not the one it's connected to.
    According to [1] it would be possible but not easily.

    [1] http://serverfault.com/a/417946
    """

    def __init__(self):
        tcp4 = ("tcp", socket.AF_INET, socket.SOCK_STREAM)
        tcp6 = ("tcp6", socket.AF_INET6, socket.SOCK_STREAM)
        udp4 = ("udp", socket.AF_INET, socket.SOCK_DGRAM)
        udp6 = ("udp6", socket.AF_INET6, socket.SOCK_DGRAM)
        unix = ("unix", socket.AF_UNIX, None)
        self.tmap = {
            "all": (tcp4, tcp6, udp4, udp6, unix),
            "tcp": (tcp4, tcp6),
            "tcp4": (tcp4,),
            "tcp6": (tcp6,),
            "udp": (udp4, udp6),
            "udp4": (udp4,),
            "udp6": (udp6,),
            "unix": (unix,),
            "inet": (tcp4, tcp6, udp4, udp6),
            "inet4": (tcp4, udp4),
            "inet6": (tcp6, udp6),
        }

    def get_proc_inodes(self, pid):
        inodes = defaultdict(list)
        for fd in os.listdir("/proc/%s/fd" % pid):
            try:
                inode = os.readlink("/proc/%s/fd/%s" % (pid, fd))
            except OSError:
                # TODO: need comment here
                continue
            else:
                if inode.startswith('socket:['):
                    # the process is using a socket
                    inode = inode[8:][:-1]
                    inodes[inode].append((pid, int(fd)))
        return inodes

    def get_all_inodes(self):
        inodes = {}
        for pid in pids():
            try:
                inodes.update(self.get_proc_inodes(pid))
            except OSError:
                # os.listdir() is gonna raise a lot of access denied
                # exceptions in case of unprivileged user; that's fine
                # as we'll just end up returning a connection with PID
                # and fd set to None anyway.
                # Both netstat -an and lsof does the same so it's
                # unlikely we can do any better.
                # ENOENT just means a PID disappeared on us.
                err = sys.exc_info()[1]
                if err.errno not in (errno.ENOENT, errno.EPERM, errno.EACCES):
                    raise
        return inodes

    def decode_address(self, addr, family):
        """Accept an "ip:port" address as displayed in /proc/net/*
        and convert it into a human readable form, like:

        "0500000A:0016" -> ("10.0.0.5", 22)
        "0000000000000000FFFF00000100007F:9E49" -> ("::ffff:127.0.0.1", 40521)

        The IP address portion is a little or big endian four-byte
        hexadecimal number; that is, the least significant byte is listed
        first, so we need to reverse the order of the bytes to convert it
        to an IP address.
        The port is represented as a two-byte hexadecimal number.

        Reference:
        http://linuxdevcenter.com/pub/a/linux/2000/11/16/LinuxAdmin.html
        """
        ip, port = addr.split(':')
        port = int(port, 16)
        if PY3:
            ip = ip.encode('ascii')
        # this usually refers to a local socket in listen mode with
        # no end-points connected
        if not port:
            return ()
        if family == socket.AF_INET:
            # see: http://code.google.com/p/psutil/issues/detail?id=201
            if sys.byteorder == 'little':
                ip = socket.inet_ntop(family, base64.b16decode(ip)[::-1])
            else:
                ip = socket.inet_ntop(family, base64.b16decode(ip))
        else:  # IPv6
            # old version - let's keep it, just in case...
            # ip = ip.decode('hex')
            # return socket.inet_ntop(socket.AF_INET6,
            #          ''.join(ip[i:i+4][::-1] for i in range(0, 16, 4)))
            ip = base64.b16decode(ip)
            # see: http://code.google.com/p/psutil/issues/detail?id=201
            if sys.byteorder == 'little':
                ip = socket.inet_ntop(
                    socket.AF_INET6,
                    struct.pack('>4I', *struct.unpack('<4I', ip)))
            else:
                ip = socket.inet_ntop(
                    socket.AF_INET6,
                    struct.pack('<4I', *struct.unpack('<4I', ip)))
        return (ip, port)

    def process_inet(self, file, family, type_, inodes, filter_pid=None):
        """Parse /proc/net/tcp* and /proc/net/udp* files."""
        if file.endswith('6') and not os.path.exists(file):
            # IPv6 not supported
            return
        f = open(file, 'rt')
        try:
            f.readline()  # skip the first line
            for line in f:
                _, laddr, raddr, status, _, _, _, _, _, inode = \
                    line.split()[:10]
                if inode in inodes:
                    # We assume inet sockets are unique, so we error
                    # out if there are multiple references to the
                    # same inode. We won't do this for UNIX sockets.
                    if len(inodes[inode]) > 1 and type_ != socket.AF_UNIX:
                        raise ValueError("ambiguos inode with multiple "
                                         "PIDs references")
                    pid, fd = inodes[inode][0]
                else:
                    pid, fd = None, -1
                if filter_pid is not None and filter_pid != pid:
                    continue
                else:
                    if type_ == socket.SOCK_STREAM:
                        status = TCP_STATUSES[status]
                    else:
                        status = _common.CONN_NONE
                    laddr = self.decode_address(laddr, family)
                    raddr = self.decode_address(raddr, family)
                    yield (fd, family, type_, laddr, raddr, status, pid)
        finally:
            f.close()

    def process_unix(self, file, family, inodes, filter_pid=None):
        """Parse /proc/net/unix files."""
        f = open(file, 'rt')
        try:
            f.readline()  # skip the first line
            for line in f:
                tokens = line.split()
                _, _, _, _, type_, _, inode = tokens[0:7]
                if inode in inodes:
                    # With UNIX sockets we can have a single inode
                    # referencing many file descriptors.
                    pairs = inodes[inode]
                else:
                    pairs = [(None, -1)]
                for pid, fd in pairs:
                    if filter_pid is not None and filter_pid != pid:
                        continue
                    else:
                        if len(tokens) == 8:
                            path = tokens[-1]
                        else:
                            path = ""
                        type_ = int(type_)
                        raddr = None
                        status = _common.CONN_NONE
                        yield (fd, family, type_, path, raddr, status, pid)
        finally:
            f.close()

    def retrieve(self, kind, pid=None):
        if kind not in self.tmap:
            raise ValueError("invalid %r kind argument; choose between %s"
                             % (kind, ', '.join([repr(x) for x in self.tmap])))
        if pid is not None:
            inodes = self.get_proc_inodes(pid)
            if not inodes:
                # no connections for this process
                return []
        else:
            inodes = self.get_all_inodes()
        ret = []
        for f, family, type_ in self.tmap[kind]:
            if family in (socket.AF_INET, socket.AF_INET6):
                ls = self.process_inet(
                    "/proc/net/%s" % f, family, type_, inodes, filter_pid=pid)
            else:
                ls = self.process_unix(
                    "/proc/net/%s" % f, family, inodes, filter_pid=pid)
            for fd, family, type_, laddr, raddr, status, bound_pid in ls:
                if pid:
                    conn = _common.pconn(fd, family, type_, laddr, raddr,
                                         status)
                else:
                    conn = _common.sconn(fd, family, type_, laddr, raddr,
                                         status, bound_pid)
                ret.append(conn)
        return ret


_connections = Connections()


def net_connections(kind='inet'):
    """Return system-wide open connections."""
    return _connections.retrieve(kind)


def net_io_counters():
    """Return network I/O statistics for every network interface
    installed on the system as a dict of raw tuples.
    """
    f = open("/proc/net/dev", "rt")
    try:
        lines = f.readlines()
    finally:
        f.close()

    retdict = {}
    for line in lines[2:]:
        colon = line.rfind(':')
        assert colon > 0, repr(line)
        name = line[:colon].strip()
        fields = line[colon + 1:].strip().split()
        bytes_recv = int(fields[0])
        packets_recv = int(fields[1])
        errin = int(fields[2])
        dropin = int(fields[3])
        bytes_sent = int(fields[8])
        packets_sent = int(fields[9])
        errout = int(fields[10])
        dropout = int(fields[11])
        retdict[name] = (bytes_sent, bytes_recv, packets_sent, packets_recv,
                         errin, errout, dropin, dropout)
    return retdict


# --- disks

def disk_io_counters():
    """Return disk I/O statistics for every disk installed on the
    system as a dict of raw tuples.
    """
    # man iostat states that sectors are equivalent with blocks and
    # have a size of 512 bytes since 2.4 kernels. This value is
    # needed to calculate the amount of disk I/O in bytes.
    SECTOR_SIZE = 512

    # determine partitions we want to look for
    partitions = []
    f = open("/proc/partitions", "rt")
    try:
        lines = f.readlines()[2:]
    finally:
        f.close()
    for line in reversed(lines):
        _, _, _, name = line.split()
        if name[-1].isdigit():
            # we're dealing with a partition (e.g. 'sda1'); 'sda' will
            # also be around but we want to omit it
            partitions.append(name)
        else:
            if not partitions or not partitions[-1].startswith(name):
                # we're dealing with a disk entity for which no
                # partitions have been defined (e.g. 'sda' but
                # 'sda1' was not around), see:
                # http://code.google.com/p/psutil/issues/detail?id=338
                partitions.append(name)
    #
    retdict = {}
    f = open("/proc/diskstats", "rt")
    try:
        lines = f.readlines()
    finally:
        f.close()
    for line in lines:
        # http://www.mjmwired.net/kernel/Documentation/iostats.txt
        _, _, name, reads, _, rbytes, rtime, writes, _, wbytes, wtime = \
            line.split()[:11]
        if name in partitions:
            rbytes = int(rbytes) * SECTOR_SIZE
            wbytes = int(wbytes) * SECTOR_SIZE
            reads = int(reads)
            writes = int(writes)
            rtime = int(rtime)
            wtime = int(wtime)
            retdict[name] = (reads, writes, rbytes, wbytes, rtime, wtime)
    return retdict


def disk_partitions(all=False):
    """Return mounted disk partitions as a list of nameduples"""
    phydevs = []
    f = open("/proc/filesystems", "r")
    try:
        for line in f:
            if not line.startswith("nodev"):
                phydevs.append(line.strip())
    finally:
        f.close()

    retlist = []
    partitions = cext.disk_partitions()
    for partition in partitions:
        device, mountpoint, fstype, opts = partition
        if device == 'none':
            device = ''
        if not all:
            if device == '' or fstype not in phydevs:
                continue
        ntuple = _common.sdiskpart(device, mountpoint, fstype, opts)
        retlist.append(ntuple)
    return retlist


disk_usage = _psposix.disk_usage


# --- decorators

def wrap_exceptions(fun):
    """Decorator which translates bare OSError and IOError exceptions
    into NoSuchProcess and AccessDenied.
    """
    @wraps(fun)
    def wrapper(self, *args, **kwargs):
        try:
            return fun(self, *args, **kwargs)
        except EnvironmentError:
            # support for private module import
            if NoSuchProcess is None or AccessDenied is None:
                raise
            # ENOENT (no such file or directory) gets raised on open().
            # ESRCH (no such process) can get raised on read() if
            # process is gone in meantime.
            err = sys.exc_info()[1]
            if err.errno in (errno.ENOENT, errno.ESRCH):
                raise NoSuchProcess(self.pid, self._name)
            if err.errno in (errno.EPERM, errno.EACCES):
                raise AccessDenied(self.pid, self._name)
            raise
    return wrapper


class Process(object):
    """Linux process implementation."""

    __slots__ = ["pid", "_name"]

    def __init__(self, pid):
        self.pid = pid
        self._name = None

    @wrap_exceptions
    def name(self):
        fname = "/proc/%s/stat" % self.pid
        if PY3:
            f = open(fname, "rt", encoding=DEFAULT_ENCODING)
        else:
            f = open(fname, "rt")
        try:
            name = f.read().split(' ')[1].replace('(', '').replace(')', '')
        finally:
            f.close()
        # XXX - gets changed later and probably needs refactoring
        return name

    def exe(self):
        try:
            exe = os.readlink("/proc/%s/exe" % self.pid)
        except (OSError, IOError):
            err = sys.exc_info()[1]
            if err.errno == errno.ENOENT:
                # no such file error; might be raised also if the
                # path actually exists for system processes with
                # low pids (about 0-20)
                if os.path.lexists("/proc/%s" % self.pid):
                    return ""
                else:
                    # ok, it is a process which has gone away
                    raise NoSuchProcess(self.pid, self._name)
            if err.errno in (errno.EPERM, errno.EACCES):
                raise AccessDenied(self.pid, self._name)
            raise

        # readlink() might return paths containing null bytes ('\x00').
        # Certain names have ' (deleted)' appended. Usually this is
        # bogus as the file actually exists. Either way that's not
        # important as we don't want to discriminate executables which
        # have been deleted.
        exe = exe.split('\x00')[0]
        if exe.endswith(' (deleted)') and not os.path.exists(exe):
            exe = exe[:-10]
        return exe

    @wrap_exceptions
    def cmdline(self):
        fname = "/proc/%s/cmdline" % self.pid
        if PY3:
            f = open(fname, "rt", encoding=DEFAULT_ENCODING)
        else:
            f = open(fname, "rt")
        try:
            # return the args as a list
            return [x for x in f.read().split('\x00') if x]
        finally:
            f.close()

    @wrap_exceptions
    def terminal(self):
        tmap = _psposix._get_terminal_map()
        f = open("/proc/%s/stat" % self.pid, 'rb')
        try:
            tty_nr = int(f.read().split(b(' '))[6])
        finally:
            f.close()
        try:
            return tmap[tty_nr]
        except KeyError:
            return None

    if os.path.exists('/proc/%s/io' % os.getpid()):
        @wrap_exceptions
        def io_counters(self):
            fname = "/proc/%s/io" % self.pid
            f = open(fname, 'rb')
            SYSCR, SYSCW = b("syscr"), b("syscw")
            READ_BYTES, WRITE_BYTES = b("read_bytes"), b("write_bytes")
            try:
                rcount = wcount = rbytes = wbytes = None
                for line in f:
                    if rcount is None and line.startswith(SYSCR):
                        rcount = int(line.split()[1])
                    elif wcount is None and line.startswith(SYSCW):
                        wcount = int(line.split()[1])
                    elif rbytes is None and line.startswith(READ_BYTES):
                        rbytes = int(line.split()[1])
                    elif wbytes is None and line.startswith(WRITE_BYTES):
                        wbytes = int(line.split()[1])
                for x in (rcount, wcount, rbytes, wbytes):
                    if x is None:
                        raise NotImplementedError(
                            "couldn't read all necessary info from %r" % fname)
                return _common.pio(rcount, wcount, rbytes, wbytes)
            finally:
                f.close()
    else:
        def io_counters(self):
            raise NotImplementedError("couldn't find /proc/%s/io (kernel "
                                      "too old?)" % self.pid)

    @wrap_exceptions
    def cpu_times(self):
        f = open("/proc/%s/stat" % self.pid, 'rb')
        try:
            st = f.read().strip()
        finally:
            f.close()
        # ignore the first two values ("pid (exe)")
        st = st[st.find(b(')')) + 2:]
        values = st.split(b(' '))
        utime = float(values[11]) / CLOCK_TICKS
        stime = float(values[12]) / CLOCK_TICKS
        return _common.pcputimes(utime, stime)

    @wrap_exceptions
    def wait(self, timeout=None):
        try:
            return _psposix.wait_pid(self.pid, timeout)
        except _psposix.TimeoutExpired:
            # support for private module import
            if TimeoutExpired is None:
                raise
            raise TimeoutExpired(timeout, self.pid, self._name)

    @wrap_exceptions
    def create_time(self):
        f = open("/proc/%s/stat" % self.pid, 'rb')
        try:
            st = f.read().strip()
        finally:
            f.close()
        # ignore the first two values ("pid (exe)")
        st = st[st.rfind(b(')')) + 2:]
        values = st.split(b(' '))
        # According to documentation, starttime is in field 21 and the
        # unit is jiffies (clock ticks).
        # We first divide it for clock ticks and then add uptime returning
        # seconds since the epoch, in UTC.
        # Also use cached value if available.
        bt = BOOT_TIME or boot_time()
        return (float(values[19]) / CLOCK_TICKS) + bt

    @wrap_exceptions
    def memory_info(self):
        f = open("/proc/%s/statm" % self.pid, 'rb')
        try:
            vms, rss = f.readline().split()[:2]
            return _common.pmem(int(rss) * PAGESIZE,
                                int(vms) * PAGESIZE)
        finally:
            f.close()

    @wrap_exceptions
    def memory_info_ex(self):
        #  ============================================================
        # | FIELD  | DESCRIPTION                         | AKA  | TOP  |
        #  ============================================================
        # | rss    | resident set size                   |      | RES  |
        # | vms    | total program size                  | size | VIRT |
        # | shared | shared pages (from shared mappings) |      | SHR  |
        # | text   | text ('code')                       | trs  | CODE |
        # | lib    | library (unused in Linux 2.6)       | lrs  |      |
        # | data   | data + stack                        | drs  | DATA |
        # | dirty  | dirty pages (unused in Linux 2.6)   | dt   |      |
        #  ============================================================
        f = open("/proc/%s/statm" % self.pid, "rb")
        try:
            vms, rss, shared, text, lib, data, dirty = \
                [int(x) * PAGESIZE for x in f.readline().split()[:7]]
        finally:
            f.close()
        return pextmem(rss, vms, shared, text, lib, data, dirty)

    if os.path.exists('/proc/%s/smaps' % os.getpid()):
        def memory_maps(self):
            """Return process's mapped memory regions as a list of nameduples.
            Fields are explained in 'man proc'; here is an updated (Apr 2012)
            version: http://goo.gl/fmebo
            """
            f = None
            try:
                f = open("/proc/%s/smaps" % self.pid, "rt")
                first_line = f.readline()
                current_block = [first_line]

                def get_blocks():
                    data = {}
                    for line in f:
                        fields = line.split(None, 5)
                        if not fields[0].endswith(':'):
                            # new block section
                            yield (current_block.pop(), data)
                            current_block.append(line)
                        else:
                            try:
                                data[fields[0]] = int(fields[1]) * 1024
                            except ValueError:
                                if fields[0].startswith('VmFlags:'):
                                    # see issue #369
                                    continue
                                else:
                                    raise ValueError("don't know how to inte"
                                                     "rpret line %r" % line)
                    yield (current_block.pop(), data)

                if first_line:  # smaps file can be empty
                    for header, data in get_blocks():
                        hfields = header.split(None, 5)
                        try:
                            addr, perms, offset, dev, inode, path = hfields
                        except ValueError:
                            addr, perms, offset, dev, inode, path = \
                                hfields + ['']
                        if not path:
                            path = '[anon]'
                        else:
                            path = path.strip()
                        yield (addr, perms, path,
                               data['Rss:'],
                               data.get('Size:', 0),
                               data.get('Pss:', 0),
                               data.get('Shared_Clean:', 0),
                               data.get('Shared_Dirty:', 0),
                               data.get('Private_Clean:', 0),
                               data.get('Private_Dirty:', 0),
                               data.get('Referenced:', 0),
                               data.get('Anonymous:', 0),
                               data.get('Swap:', 0))
                f.close()
            except EnvironmentError:
                # XXX - Can't use wrap_exceptions decorator as we're
                # returning a generator;  this probably needs some
                # refactoring in order to avoid this code duplication.
                if f is not None:
                    f.close()
                err = sys.exc_info()[1]
                if err.errno in (errno.ENOENT, errno.ESRCH):
                    raise NoSuchProcess(self.pid, self._name)
                if err.errno in (errno.EPERM, errno.EACCES):
                    raise AccessDenied(self.pid, self._name)
                raise
            except:
                if f is not None:
                    f.close()
                raise
            f.close()

    else:
        def memory_maps(self, ext):
            msg = "couldn't find /proc/%s/smaps; kernel < 2.6.14 or "  \
                  "CONFIG_MMU kernel configuration option is not enabled" \
                  % self.pid
            raise NotImplementedError(msg)

    @wrap_exceptions
    def cwd(self):
        # readlink() might return paths containing null bytes causing
        # problems when used with other fs-related functions (os.*,
        # open(), ...)
        path = os.readlink("/proc/%s/cwd" % self.pid)
        return path.replace('\x00', '')

    @wrap_exceptions
    def num_ctx_switches(self):
        vol = unvol = None
        f = open("/proc/%s/status" % self.pid, "rb")
        VOLUNTARY = b("voluntary_ctxt_switches")
        NON_VOLUNTARY = b("nonvoluntary_ctxt_switches")
        try:
            for line in f:
                if line.startswith(VOLUNTARY):
                    vol = int(line.split()[1])
                elif line.startswith(NON_VOLUNTARY):
                    unvol = int(line.split()[1])
                if vol is not None and unvol is not None:
                    return _common.pctxsw(vol, unvol)
            raise NotImplementedError(
                "'voluntary_ctxt_switches' and 'nonvoluntary_ctxt_switches'"
                "fields were not found in /proc/%s/status; the kernel is "
                "probably older than 2.6.23" % self.pid)
        finally:
            f.close()

    @wrap_exceptions
    def num_threads(self):
        f = open("/proc/%s/status" % self.pid, "rb")
        try:
            THREADS = b("Threads:")
            for line in f:
                if line.startswith(THREADS):
                    return int(line.split()[1])
            raise NotImplementedError("line not found")
        finally:
            f.close()

    @wrap_exceptions
    def threads(self):
        thread_ids = os.listdir("/proc/%s/task" % self.pid)
        thread_ids.sort()
        retlist = []
        hit_enoent = False
        for thread_id in thread_ids:
            try:
                f = open("/proc/%s/task/%s/stat" % (self.pid, thread_id), 'rb')
            except EnvironmentError:
                err = sys.exc_info()[1]
                if err.errno == errno.ENOENT:
                    # no such file or directory; it means thread
                    # disappeared on us
                    hit_enoent = True
                    continue
                raise
            try:
                st = f.read().strip()
            finally:
                f.close()
            # ignore the first two values ("pid (exe)")
            st = st[st.find(b(')')) + 2:]
            values = st.split(b(' '))
            utime = float(values[11]) / CLOCK_TICKS
            stime = float(values[12]) / CLOCK_TICKS
            ntuple = _common.pthread(int(thread_id), utime, stime)
            retlist.append(ntuple)
        if hit_enoent:
            # raise NSP if the process disappeared on us
            os.stat('/proc/%s' % self.pid)
        return retlist

    @wrap_exceptions
    def nice_get(self):
        #f = open('/proc/%s/stat' % self.pid, 'r')
        # try:
        #   data = f.read()
        #   return int(data.split()[18])
        # finally:
        #   f.close()

        # Use C implementation
        return _psutil_posix.getpriority(self.pid)

    @wrap_exceptions
    def nice_set(self, value):
        return _psutil_posix.setpriority(self.pid, value)

    @wrap_exceptions
    def cpu_affinity_get(self):
        from_bitmask = lambda x: [i for i in range(64) if (1 << i) & x]
        bitmask = cext.proc_cpu_affinity_get(self.pid)
        return from_bitmask(bitmask)

    @wrap_exceptions
    def cpu_affinity_set(self, cpus):
        try:
            cext.proc_cpu_affinity_set(self.pid, cpus)
        except OSError:
            err = sys.exc_info()[1]
            if err.errno == errno.EINVAL:
                allcpus = tuple(range(len(per_cpu_times())))
                for cpu in cpus:
                    if cpu not in allcpus:
                        raise ValueError("invalid CPU #%i (choose between %s)"
                                         % (cpu, allcpus))
            raise

    # only starting from kernel 2.6.13
    if hasattr(cext, "proc_ioprio_get"):

        @wrap_exceptions
        def ionice_get(self):
            ioclass, value = cext.proc_ioprio_get(self.pid)
            return _common.pionice(ioclass, value)

        @wrap_exceptions
        def ionice_set(self, ioclass, value):
            if ioclass in (IOPRIO_CLASS_NONE, None):
                if value:
                    msg = "can't specify value with IOPRIO_CLASS_NONE"
                    raise ValueError(msg)
                ioclass = IOPRIO_CLASS_NONE
                value = 0
            if ioclass in (IOPRIO_CLASS_RT, IOPRIO_CLASS_BE):
                if value is None:
                    value = 4
            elif ioclass == IOPRIO_CLASS_IDLE:
                if value:
                    msg = "can't specify value with IOPRIO_CLASS_IDLE"
                    raise ValueError(msg)
                value = 0
            else:
                value = 0
            if not 0 <= value <= 8:
                raise ValueError(
                    "value argument range expected is between 0 and 8")
            return cext.proc_ioprio_set(self.pid, ioclass, value)

    if HAS_PRLIMIT:
        @wrap_exceptions
        def rlimit(self, resource, limits=None):
            # if pid is 0 prlimit() applies to the calling process and
            # we don't want that
            if self.pid == 0:
                raise ValueError("can't use prlimit() against PID 0 process")
            if limits is None:
                # get
                return cext.linux_prlimit(self.pid, resource)
            else:
                # set
                if len(limits) != 2:
                    raise ValueError(
                        "second argument must be a (soft, hard) tuple")
                soft, hard = limits
                cext.linux_prlimit(self.pid, resource, soft, hard)

    @wrap_exceptions
    def status(self):
        f = open("/proc/%s/status" % self.pid, 'rb')
        try:
            STATE = b("State:")
            for line in f:
                if line.startswith(STATE):
                    letter = line.split()[1]
                    if PY3:
                        letter = letter.decode()
                    # XXX is '?' legit? (we're not supposed to return
                    # it anyway)
                    return PROC_STATUSES.get(letter, '?')
        finally:
            f.close()

    @wrap_exceptions
    def open_files(self):
        retlist = []
        files = os.listdir("/proc/%s/fd" % self.pid)
        hit_enoent = False
        for fd in files:
            file = "/proc/%s/fd/%s" % (self.pid, fd)
            if os.path.islink(file):
                try:
                    file = os.readlink(file)
                except OSError:
                    # ENOENT == file which is gone in the meantime
                    err = sys.exc_info()[1]
                    if err.errno == errno.ENOENT:
                        hit_enoent = True
                        continue
                    raise
                else:
                    # If file is not an absolute path there's no way
                    # to tell whether it's a regular file or not,
                    # so we skip it. A regular file is always supposed
                    # to be absolutized though.
                    if file.startswith('/') and isfile_strict(file):
                        ntuple = _common.popenfile(file, int(fd))
                        retlist.append(ntuple)
        if hit_enoent:
            # raise NSP if the process disappeared on us
            os.stat('/proc/%s' % self.pid)
        return retlist

    @wrap_exceptions
    def connections(self, kind='inet'):
        ret = _connections.retrieve(kind, self.pid)
        # raise NSP if the process disappeared on us
        os.stat('/proc/%s' % self.pid)
        return ret

    @wrap_exceptions
    def num_fds(self):
        return len(os.listdir("/proc/%s/fd" % self.pid))

    @wrap_exceptions
    def ppid(self):
        f = open("/proc/%s/status" % self.pid, 'rb')
        try:
            PPID = b("PPid:")
            for line in f:
                if line.startswith(PPID):
                    # PPid: nnnn
                    return int(line.split()[1])
            raise NotImplementedError("line not found")
        finally:
            f.close()

    @wrap_exceptions
    def uids(self):
        f = open("/proc/%s/status" % self.pid, 'rb')
        try:
            UID = b('Uid:')
            for line in f:
                if line.startswith(UID):
                    _, real, effective, saved, fs = line.split()
                    return _common.puids(int(real), int(effective), int(saved))
            raise NotImplementedError("line not found")
        finally:
            f.close()

    @wrap_exceptions
    def gids(self):
        f = open("/proc/%s/status" % self.pid, 'rb')
        try:
            GID = b('Gid:')
            for line in f:
                if line.startswith(GID):
                    _, real, effective, saved, fs = line.split()
                    return _common.pgids(int(real), int(effective), int(saved))
            raise NotImplementedError("line not found")
        finally:
            f.close()
