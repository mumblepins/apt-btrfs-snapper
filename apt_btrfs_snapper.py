# Copyright (C) 2011 Canonical
#
# Author:
#  Michael Vogt
#
# This program is free software; you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free Software
# Foundation; version 3.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE.  See the GNU General Public License for more
# details.
#
# You should have received a copy of the GNU General Public License along with
# this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA

from __future__ import print_function, unicode_literals

import datetime
import os
import subprocess
import sys
import time
from distutils.spawn import find_executable


class AptBtrfsSnapperError(Exception):
    pass


class AptBtrfsNotSupportedError(AptBtrfsSnapperError):
    pass


class AptBtrfsRootWithNoatimeError(AptBtrfsSnapperError):
    pass


class LowLevelCommands(object):
    """ lowlevel commands invoked to perform various tasks like
        interact with mount and btrfs tools
    """
    CLEANUP_MODE = 'number'

    def _btrfs_snapshot_list(self, return_split=True, max_fields=5):
        ret = subprocess.check_output(["snapper", "list", "-t", "pre-post"],
                                      universal_newlines=True)
        lines = ret.split("\n")
        lines = iter(lines)
        newlines = []
        headerlines = []

        # Include Header
        fields = next(lines).split('|')[0:max_fields]
        header = '|'.join(fields)
        cut_len = len(header)
        headerlines.append(header.replace("Description", "Name"))

        # Separator line
        headerlines.append(next(lines)[0:cut_len])

        for line in lines:
            if AptBtrfsSnapper.SNAP_PREFIX not in line:
                continue
            text = line[0:cut_len]

            if not return_split:
                newlines.append(text)
                continue

            split = [a.strip() for a in line.split("|")]
            if len(split) < 6:
                continue
            row = {
                'name': split[4],
                'id': split[1],
                'type': 'pre',
                'cleanup': self.CLEANUP_MODE,
                'user_data': split[5],
                'date': split[2],
                'text': text,
                'pre_id': split[0]
            }
            newlines.append(row)
        return headerlines, newlines

    def btrfs_snapshot_list(self):
        headerlines, items = self._btrfs_snapshot_list(True)
        return headerlines, items

    def btrfs_snapshot_list_pre_post(self):
        headerlines, newlines = self._btrfs_snapshot_list(False)
        return "\n".join(headerlines + newlines)

    def btrfs_subvolume_snapshot(self, description, ctype, pre_id=-1):
        arguments = ["snapper", "create", "-d", description, "-c",
                     self.CLEANUP_MODE, "--print-number", "-t", ctype]
        if pre_id != -1:
            arguments.append("--pre-number")
            arguments.append("{}".format(pre_id))
        ret = subprocess.check_output(arguments, universal_newlines=True)
        return ret.strip()

    def btrfs_delete_snapshot(self, sid):
        ret = subprocess.call(["snapper", "delete", sid])
        return ret == 0

    def btrfs_restore_snapshot(self, sid):
        ret = subprocess.call(["snapper", "undochange", sid + "..0"])
        return ret == 0

    def btrfs_snapshot_diff(self, sid, id2):
        ret = 0
        try:
            ret = subprocess.check_output(
                ["snapper", "status", sid + ".." + id2],
                universal_newlines=True)
        except subprocess.CalledProcessError:
            print("The snapshots you supplied des not appear to exist.")
        return ret

    def btrfs_snapshot_userdata(self, sid, data):
        if len(data.strip()) < 2:
            return False

        data = "Packages to be installed=" + data
        ret = subprocess.call(
            ['snapper', 'modify', '--userdata', data.strip(), sid])
        return ret == 0


class AptBtrfsSnapper(object):
    """ the high level object that interacts with the snapshot system """

    # normal snapshot
    SNAP_PREFIX = "apt-snapper-"
    SNAPPER_TIME = "%a %d %b %Y %I:%M:%S %p %Z"
    PRE_ID_FILE = "/var/run/apt-btrfs-snapper-pre-id"

    def __init__(self):
        self.commands = LowLevelCommands()

    def snapshots_supported(self):
        """ verify that the system supports snapper
            this is a limited check. more could be done
            to ensure 100% that the user has snapper
            configured on the root volume
        """
        # check for the helper binary
        if not find_executable("snapper"):
            return False

        DIR = '/etc/snapper/configs'

        # does a snapper config exist
        if not os.path.exists(DIR):
            return False
        return len([name for name in os.listdir(DIR)])

    def _get_now_str(self):
        return datetime.datetime.now().replace(microsecond=0).isoformat(
            str('_'))

    def create_btrfs_root_snapshot(self, additional_prefix="", stype="single",
                                   sid=-1):
        snap_id = self._get_now_str()
        name = self.SNAP_PREFIX + additional_prefix + snap_id
        res = self.commands.btrfs_subvolume_snapshot(name, stype, sid)
        print("Created " + stype + " snapshot with snapper as " + name +
              " with id " + res)
        return res

    def create_btrfs_root_snapshot_pre(self, additional_prefix=""):
        if os.path.exists(self.PRE_ID_FILE):
            id_file = open(self.PRE_ID_FILE, 'r')
            existing = id_file.read()
            if existing:
                existing = int(existing.strip())
                self.delete_snapshot(existing)
                print("Deleted orphaned snapshot %d" % existing)
            id_file.close()
            os.remove(self.PRE_ID_FILE)

        id_file = open(self.PRE_ID_FILE, 'w+')
        sid = self.create_btrfs_root_snapshot(additional_prefix, "pre")
        id_file.truncate()
        id_file.write(str(sid).strip())
        id_file.close()
        return sid

    def create_btrfs_root_snapshot_post(self, additional_prefix=""):
        try:
            id_file = open(self.PRE_ID_FILE, 'r')
        except (OSError, IOError):
            print("The pre snapshot ID was not found")
            return False

        sid = int(id_file.read().strip())
        sid = self.create_btrfs_root_snapshot(additional_prefix, "post", sid)
        os.remove(id_file.name)
        id_file.close()
        return sid

    def get_btrfs_root_snapshots_list(self, older_than=0, only_id=False):
        """ get the list of available snapshot
            If "older_then" is given (in unixtime format) it will only include
            snapshots that are older then the given date)
        """
        s_list = []
        if older_than == 0:
            older_than = time.time()

        headerlines, items = self.commands.btrfs_snapshot_list()
        for item in items:
            mtime = time.mktime(
                time.strptime(item['date'], self.SNAPPER_TIME))
            if (older_than > mtime):
                s_list.append(item['id'] if only_id else item)

        if only_id:
            return s_list

        items = []
        for item in s_list:
            items.append(item['text'])

        return headerlines + items

    def print_btrfs_root_snapshots(self):
        print(
            "\nNote: Use a tool like snapper-gui to see what packages were "
            "installed")
        print("Available snapshots:")
        print(self.commands.btrfs_snapshot_list_pre_post())
        return True

    def _parse_older_than_to_unixtime(self, timefmt):
        now = time.time()
        if not timefmt.endswith("d"):
            raise Exception("Please specify time in days (e.g. 10d)")
        days = int(timefmt[:-1])
        return now - (days * 24 * 60 * 60)

    def print_btrfs_root_snapshots_older_than(self, timefmt):
        older_than_unixtime = self._parse_older_than_to_unixtime(timefmt)
        try:
            print("Available snapshots older than '%s':" % timefmt)
            print("  \n".join(self.get_btrfs_root_snapshots_list(
                older_than=older_than_unixtime)))
        except AptBtrfsRootWithNoatimeError:
            sys.stderr.write("Error: fstab option 'noatime' incompatible "
                             "with option \n")
            return False
        return True

    def clean_btrfs_root_snapshots_older_than(self, timefmt):
        res = True
        older_than_unixtime = self._parse_older_than_to_unixtime(timefmt)
        try:
            for snap in self.get_btrfs_root_snapshots_list(
                    older_than=older_than_unixtime, only_id=True):
                res &= self.delete_snapshot(snap)
        except AptBtrfsRootWithNoatimeError:
            sys.stderr.write("Error: fstab option 'noatime' incompatible with "
                             "option \n")
            return False
        return res

    def show_diff(self, snapshot, snapshot2):
        id = self.convert_name_to_id(snapshot)
        id2 = self.convert_name_to_id(snapshot2)
        print("Please wait...\n")
        if id != -1:
            diff = self.commands.btrfs_snapshot_diff(id, id2)
            diff = diff.split("\n")
            for line in diff:
                items = line.split(" ")
                if (len(items[0]) < 1):
                    continue
                if items[0][0] == "+" or items[0][0] == "c":
                    bytes_cnt = (os.path.getsize(items[1])
                                 if os.path.exists(items[1]) else 0)
                else:
                    bytes_cnt = 0
                line = (items[0][0] + "   " +
                        (self.humansize(bytes_cnt)
                         if bytes_cnt > 0 else "").ljust(10) +
                        " " + items[1])

                print(line)

            return True
        return False

    def command_set_default(self, snapshot_name):
        res = self.set_default(snapshot_name)
        return res

    def convert_name_to_id(self, snapshot_name):
        sid = -1
        snapshot_name = snapshot_name.strip()
        try:
            int(snapshot_name)
            sid = snapshot_name
        except ValueError:
            _, slist = self.commands.btrfs_snapshot_list()
            for item in slist:
                if item['name'].strip() == snapshot_name:
                    sid = item['id']

        if sid == -1:
            sys.stderr.write(
                "Could not find a snapshot with the supplied name or id \n")

        return sid

    def update_installed_packages(self):
        try:
            id_file = open(self.PRE_ID_FILE, 'r')
            sid = id_file.read().strip()
            if len(sid) < 1:
                return True

            id_file.close()
        except (OSError, IOError):
            return True

        data = ""
        lines = sys.stdin.readlines()
        for line in lines:
            data = data + os.path.basename(line.strip()) + "\n"
        self.commands.btrfs_snapshot_userdata(sid, data)
        return True

    def set_default(self, snapshot_name, backup=True):
        """ set new default """
        sid = self.convert_name_to_id(snapshot_name)

        if sid != -1:
            ret = self.commands.btrfs_restore_snapshot(sid)
            return ret
        return False

    def delete_snapshot(self, snapshot_name):
        res = self.commands.btrfs_delete_snapshot(snapshot_name)
        return res

    def humansize(self, nbytes):
        suffixes = ['b', 'kb', 'mb', 'gb', 'tb', 'pb']
        if nbytes == 0:
            return '0 B'
        i = 0
        while nbytes >= 1024 and i < len(suffixes) - 1:
            nbytes /= 1024.
            i += 1
        f = ('%.2f' % nbytes).rstrip('0').rstrip('.')
        return '%s%s' % (f, suffixes[i])
