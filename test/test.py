"""Copy of "all_test.py", for backwards-compatibility with CI scripts
that call "test.py".

TODO: Delete this after updating CI scripts.
"""

import unittest

from tests.kernel_tests import *
from tests.simple_notebook_tests import *
from tests.tutorial_notebook_tests import *


if __name__ == '__main__':
    unittest.main()
