"""Runs all tests that work in the docker image.

Specifically, this includes the SwiftKernelTestsPython27 test that requires a
special kernel named 'swift-with-python-2.7'.
"""

import unittest

from tests.kernel_tests import *
from tests.simple_notebook_tests import *
from tests.tutorial_notebook_tests import *


if __name__ == '__main__':
    unittest.main()
