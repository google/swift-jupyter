"""Runs fast tests."""

import unittest

from tests.kernel_tests import SwiftKernelTests, ProcessKilledTest
from tests.simple_notebook_tests import *


if __name__ == '__main__':
    unittest.main()
