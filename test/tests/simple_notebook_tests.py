"""Checks that simple notebooks behave as expected.
"""

import unittest
import os

from notebook_tester import ExecuteError
from notebook_tester import NotebookTestRunner


THIS_DIR = os.path.dirname(os.path.abspath(__file__))
NOTEBOOK_DIR = os.path.join(THIS_DIR, 'notebooks')


class SimpleNotebookTests(unittest.TestCase):
    def test_simple_successful(self):
        notebook = os.path.join(NOTEBOOK_DIR, 'simple_successful.ipynb')
        runner = NotebookTestRunner(notebook, verbose=False)
        runner.run()
        self.assertEqual([], runner.unexpected_errors)
        self.assertIn('Hello World: 3', runner.stdout[2])

    def test_intentional_compile_error(self):
        notebook = os.path.join(NOTEBOOK_DIR, 'intentional_compile_error.ipynb')
        runner = NotebookTestRunner(notebook, verbose=False)
        runner.run()
        self.assertEqual(1, len(runner.unexpected_errors))
        self.assertIsInstance(runner.unexpected_errors[0]['error'],
                              ExecuteError)
        self.assertEqual(1, runner.unexpected_errors[0]['error'].cell_index)

    def test_intentional_runtime_error(self):
        notebook = os.path.join(NOTEBOOK_DIR, 'intentional_runtime_error.ipynb')
        runner = NotebookTestRunner(notebook, verbose=False)
        runner.run()
        self.assertEqual(1, len(runner.unexpected_errors))
        self.assertIsInstance(runner.unexpected_errors[0]['error'],
                              ExecuteError)
        self.assertEqual(1, runner.unexpected_errors[0]['error'].cell_index)

    def test_install_package(self):
        notebook = os.path.join(NOTEBOOK_DIR, 'install_package.ipynb')
        runner = NotebookTestRunner(notebook, char_step=0, verbose=False)
        runner.run()
        self.assertIn('Installation complete', runner.stdout[0])
        self.assertIn('42', runner.stdout[2])

    def test_install_package_with_c(self):
        notebook = os.path.join(NOTEBOOK_DIR, 'install_package_with_c.ipynb')
        runner = NotebookTestRunner(notebook, char_step=0, verbose=False)
        runner.run()
        self.assertIn('Installation complete', runner.stdout[0])
        self.assertIn('42', runner.stdout[2])
        self.assertIn('1337', runner.stdout[3])
