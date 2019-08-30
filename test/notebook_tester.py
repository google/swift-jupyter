"""Runs notebooks.

See --help text for more information.
"""

import argparse
import nbformat
import numpy
import os
import sys
import time

from collections import defaultdict
from jupyter_client.manager import start_new_kernel


# Exception for problems that occur while executing cell.
class ExecuteException(Exception):
    def __init__(self, cell_index):
        self.cell_index = cell_index


# There was an error (that did not crash the kernel) while executing the cell.
class ExecuteError(ExecuteException):
    def __init__(self, cell_index, reply, stdout):
        super(ExecuteError, self).__init__(cell_index)
        self.reply = reply
        self.stdout = stdout

    def __str__(self):
        return 'ExecuteError at cell %d, reply:\n%s\n\nstdout:\n%s' % (
                self.cell_index, self.reply, self.stdout)


# The kernel crashed while executing the cell.
class ExecuteCrash(ExecuteException):
    def __init__(self, cell_index):
        super(ExecuteCrash, self).__init__(cell_index)

    def __str__(self):
        return 'ExecuteCrash at cell %d' % self.cell_index


# Exception for problems that occur during a completion request.
class CompleteException(Exception):
    def __init__(self, cell_index, char_index):
        self.cell_index = cell_index
        self.char_index = char_index


# There was an error (that did not crash the kernel) while processing a
# completion request.
class CompleteError(CompleteException):
    def __init__(self, cell_index, char_index):
        super(CompleteError, self).__init__(cell_index, char_index)

    def __str__(self):
        return 'CompleteError at cell %d, char %d' % (self.cell_index,
                                                      self.char_index)


# The kernel crashed while processing a completion request.
class CompleteCrash(CompleteException):
    def __init__(self, cell_index, char_index):
        super(CompleteCrash, self).__init__(cell_index, char_index)

    def __str__(self):
        return 'CompleteCrash at cell %d, char %d' % (self.cell_index,
                                                      self.char_index)


class NotebookTestRunner:
    def __init__(self, notebook, char_step=1, repeat_times=1,
                 execute_timeout=60, complete_timeout=5, verbose=True):
        """
        noteboook - path to a notebook to run the test on
        char_step - number of chars to step per completion request. 0 disables
        repeat_times - run the notebook this many times, in the same kernel
                       instance
        execute_timeout - number of seconds to wait for cell execution
        complete_timeout - number of seconds to wait for completion
        verbose - print progress, statistics, and errors
        """

        self.char_step = char_step
        self.repeat_times = repeat_times
        self.execute_timeout = execute_timeout
        self.complete_timeout = complete_timeout
        self.verbose = verbose

        notebook_dir = os.path.dirname(notebook)
        os.chdir(notebook_dir)
        nb = nbformat.read(notebook, as_version=4)

        self.code_cells = [cell for cell in nb.cells
                           if cell.cell_type == 'code' \
                           and not cell.source.startswith('#@title')]

        self.stdout = []
        self.unexpected_errors = []

    def _execute_cell(self, cell_index):
        code = self.code_cells[cell_index].source
        self._execute_code(code, cell_index)

    def _execute_code(self, code, cell_index=-1):
        self.kc.execute(code)

        # Consume all the iopub messages that the execution produced.
        stdout = ''
        while True:
            try:
                reply = self.kc.get_iopub_msg(timeout=self.execute_timeout)
            except TimeoutError:
                # Timeout usually means that the kernel has crashed.
                raise ExecuteCrash(cell_index)
            if reply['header']['msg_type'] == 'stream' and \
                    reply['content']['name'] == 'stdout':
                stdout += reply['content']['text']
            if reply['header']['msg_type'] == 'status' and \
                    reply['content']['execution_state'] == 'idle':
                break

        # Consume the shell message that the execution produced.
        try:
            reply = self.kc.get_shell_msg(timeout=self.execute_timeout)
        except TimeoutError:
            # Timeout usually means that the kernel has crashed.
            raise ExecuteCrash(cell_index)
        if reply['content']['status'] != 'ok':
            raise ExecuteError(cell_index, reply, stdout)

        if cell_index >= 0:
            self.stdout.append(stdout)

        return stdout

    def _complete(self, cell_index, char_index):
        code = self.code_cells[cell_index].source[:char_index]
        try:
            reply = self.kc.complete(code, reply=True, timeout=self.complete_timeout)
        except TimeoutError:
            # Timeout usually means that the kernel has crashed.
            raise CompleteCrash(cell_index, char_index)
        if reply['content']['status'] != 'ok':
            raise CompleteError(cell_index, char_index)

        # Consume all the iopub messages that the completion produced.
        while True:
            try:
                reply = self.kc.get_iopub_msg(timeout=self.execute_timeout)
            except TimeoutError:
                # Timeout usually means that the kernel has crashed.
                raise CompleteCrash(cell_index, char_index)
            if reply['header']['msg_type'] == 'status' and \
                    reply['content']['execution_state'] == 'idle':
                break

    def _init_kernel(self):
        km, kc = start_new_kernel(kernel_name='swift')
        self.km = km
        self.kc = kc

    # Runs each code cell in order, asking for completions in each cell along
    # the way. Raises an exception if there is an error or crash. Otherwise,
    # returns.
    def _run_notebook_once(self, failed_completions):
        for cell_index, cell in enumerate(self.code_cells):
            completion_times = []

            # Don't do completions when `char_step` is 0.
            # Don't do completions when we already have 3 completion failures
            # in this cell.
            # Otherwise, ask for a completion every `char_step` chars.
            if self.char_step > 0 and \
                    len(failed_completions[cell_index]) < 3:
                for char_index in range(0, len(cell.source), self.char_step):
                    if char_index in failed_completions[cell_index]:
                        continue
                    if self.verbose:
                        print('Cell %d/%d: completing char %d/%d' % (
                                cell_index, len(self.code_cells), char_index,
                                len(cell.source)),
                              end='\r')
                    start_time = time.time()
                    self._complete(cell_index, char_index)
                    completion_times.append(1000 * (time.time() - start_time))


            # Execute the cell.
            if self.verbose:
                print('Cell %d/%d: executing                   ' % (
                        cell_index, len(self.code_cells)),
                      end='\r')
            start_time = time.time()
            self._execute_cell(cell_index)
            execute_time = 1000 * (time.time() - start_time)

            # Report the results.
            report = 'Cell %d/%d: done' % (cell_index, len(self.code_cells))
            report += ' - execute %.0f ms' % execute_time
            if len(failed_completions[cell_index]) > 0:
                # Don't report completion timings in cells with failed
                # completions, because they might be misleading.
                report += ' - completion error(s) occurred'
            elif len(completion_times) == 0:
                report += ' - no completions performed'
            else:
                report += ' - complete p50 %.0f ms' % (
                        numpy.percentile(completion_times, 50))
                report += ' - complete p90 %.0f ms' % (
                        numpy.percentile(completion_times, 90))
                report += ' - complete p99 %.0f ms' % (
                        numpy.percentile(completion_times, 99))
            if self.verbose:
                print(report)

    def _record_error(self, e):
        cell = self.code_cells[e.cell_index]
        if hasattr(e, 'char_index'):
            code = cell.source[:e.char_index]
        else:
            code = cell.source

        error_description = {
            'error': e,
            'code': code,
        }

        if self.verbose:
            print('ERROR!\n%s\n\nCode:\n%s\n' % (e, code))
        self.unexpected_errors.append(error_description)

    def run(self):
        # map from cell index to set of char indexes where completions failed
        failed_completions = defaultdict(set)

        while True:
            self._init_kernel()
            try:
                for _ in range(self.repeat_times):
                    self._run_notebook_once(failed_completions)
                break
            except ExecuteException as ee:
                # Execution exceptions can't be recovered, so take note of the
                # error and stop the stress test.
                self._record_error(ee)
                break
            except CompleteException as ce:
                # Completion exceptions can be recovered! Restart the kernel
                # and don't ask for the broken completion next time.
                self._record_error(ce)
                failed_completions[ce.cell_index].add(ce.char_index)
            finally:
                self.km.shutdown_kernel(now=True)


def parse_args():
    parser = argparse.ArgumentParser(
            description='Executes all the cells in a Jupyter notebook, and '
                        'requests completions along the way. Records and '
                        'reports errors and kernel crashes that occur.')
    parser.add_argument('notebook',
                        help='path to a notebook to run the test on')
    parser.add_argument('--char-step', type=int, default=1,
                        help='number of chars to step per completion request. '
                             '0 disables completion requests')
    parser.add_argument('--repeat-times', type=int, default=1,
                        help='run the notebook this many times, in the same '
                             'kernel instance')
    parser.add_argument('--execute-timeout', type=int, default=15,
                        help='number of seconds to wait for cell execution')
    parser.add_argument('--complete-timeout', type=int, default=5,
                        help='number of seconds to wait for completion')
    return parser.parse_args()


def _main():
    args = parse_args()
    runner = NotebookTestRunner(**args.__dict__)
    runner.run()
    print(runner.unexpected_errors)


if __name__ == '__main__':
    _main()
