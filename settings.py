#!/usr/bin/env python

from distutils.core import setup

setup(name='Desktopapp',
      version='1.0',
      description='Python Distribution Utilities',
      author='Javier Pérez',
      package_dir = {'Desktopapp': 'src'},
      scripts=['main.py']
     )