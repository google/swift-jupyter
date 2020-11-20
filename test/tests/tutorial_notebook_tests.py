"""Checks that tutorial notebooks behave as expected.
"""

import unittest
import os
import shutil
import tempfile

from flaky import flaky

from notebook_tester import NotebookTestRunner


class TutorialNotebookTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp_dir = tempfile.mkdtemp()
        git_url = 'https://github.com/tensorflow/swift.git'
        os.system('git clone %s %s -b jupyter-test-branch' % (git_url, cls.tmp_dir))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp_dir)

    def test_iris(self):
        notebook = os.path.join(self.tmp_dir, 'docs', 'site', 'tutorials',
                                'model_training_walkthrough.ipynb')
        # execute_wait=10 seems to help prevent triggering
        # https://github.com/google/swift-jupyter/issues/123
        runner = NotebookTestRunner(notebook, execute_wait=10, verbose=False)
        runner.run()
        self.assertEqual([], runner.unexpected_errors)
        all_stdout = '\n\n'.join(runner.stdout)
        self.assertIn('Epoch 100:', all_stdout)
        self.assertIn('Example 2 prediction:', all_stdout)
