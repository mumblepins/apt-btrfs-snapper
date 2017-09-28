#!/usr/bin/env python

from distutils.core import setup

from DistUtilsExtra.command import build_i18n, build_extra

setup(name='apt-btrfs-snapper',
      version='0.1',
      author='Michael Vogt',
      url='http://launchpad.net/apt-btrfs-snapper',
      scripts=['apt-btrfs-snapper'],
      py_modules=['apt_btrfs_snapper'],
      cmdclass={
          "build": build_extra.build_extra,
          "build_i18n": build_i18n.build_i18n,
      }
      )
