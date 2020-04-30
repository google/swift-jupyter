"""Manually crafted tests testing specific features of the kernel.
"""

import unittest
import jupyter_kernel_test
import time

from jupyter_client.manager import start_new_kernel

# This superclass defines tests but does not run them against kernels, so that
# we can subclass this to run the same tests against different kernels.
#
# In particular, the subclasses run against kernels that interop with differnt
# versions of Python, so that we test that graphics work with different versions
# of Python.
class SwiftKernelTestsBase:
    language_name = 'swift'

    code_hello_world = 'print("hello, world!")'

    code_execute_result = [
        {'code': 'let x = 2; x', 'result': '2\n'}
    ]

    code_generate_error = 'varThatIsntDefined'

    def setUp(self):
        self.flush_channels()

    def test_graphics_matplotlib(self):
        reply, output_msgs = self.execute_helper(code="""
            %include "EnableIPythonDisplay.swift"
        """)
        self.assertEqual(reply['content']['status'], 'ok')

        reply, output_msgs = self.execute_helper(code="""
            let np = Python.import("numpy")
            let plt = Python.import("matplotlib.pyplot")
            IPythonDisplay.shell.enable_matplotlib("inline")
        """)
        self.assertEqual(reply['content']['status'], 'ok')

        reply, output_msgs = self.execute_helper(code="""
            let ys = np.arange(0, 10, 0.01)
            plt.plot(ys)
            plt.show()
        """)
        self.assertEqual(reply['content']['status'], 'ok')
        self.assertIn('image/png', output_msgs[0]['content']['data'])

    def test_extensions(self):
        reply, output_msgs = self.execute_helper(code="""
           struct Foo{}
        """)
        self.assertEqual(reply['content']['status'], 'ok')
        reply, output_msgs = self.execute_helper(code="""
           extension Foo { func f() -> Int { return 1 } }
        """)
        self.assertEqual(reply['content']['status'], 'ok')
        reply, output_msgs = self.execute_helper(code="""
           print("Value of Foo().f() is", Foo().f())
        """)
        self.assertEqual(reply['content']['status'], 'ok')
        self.assertIn("Value of Foo().f() is 1", output_msgs[0]['content']['text'])
        reply, output_msgs = self.execute_helper(code="""
        extension Foo { func f() -> Int { return 2 } }
        """)
        self.assertEqual(reply['content']['status'], 'ok')
        reply, output_msgs = self.execute_helper(code="""
           print("Value of Foo().f() is", Foo().f())
        """)
        self.assertEqual(reply['content']['status'], 'ok')
        self.assertIn("Value of Foo().f() is 2", output_msgs[0]['content']['text'])

    def test_gradient_across_cells_error(self):
        reply, output_msgs = self.execute_helper(code="""
           func square(_ x : Float) -> Float { return x * x }
        """)
        self.assertEqual(reply['content']['status'], 'ok')
        reply, output_msgs = self.execute_helper(code="""
           print("5^2 is", square(5))
        """)
        self.assertEqual(reply['content']['status'], 'ok')
        self.assertIn("5^2 is 25.0", output_msgs[0]['content']['text'])
        reply, output_msgs = self.execute_helper(code="""
           print("gradient of square at 5 is", gradient(at: 5, in: square))
        """)
        self.assertEqual(reply['content']['status'], 'error')
        self.assertIn("cannot differentiate functions that have not been marked '@differentiable'",
                      reply['content']['traceback'][0])

    def test_gradient_across_cells(self):
        reply, output_msgs = self.execute_helper(code="""
           @differentiable
           func square(_ x : Float) -> Float { return x * x }
        """)
        self.assertEqual(reply['content']['status'], 'ok')
        reply, output_msgs = self.execute_helper(code="""
           print("5^2 is", square(5))
        """)
        self.assertEqual(reply['content']['status'], 'ok')
        self.assertIn("5^2 is 25.0", output_msgs[0]['content']['text'])
        reply, output_msgs = self.execute_helper(code="""
           print("gradient of square at 5 is", gradient(at: 5, in: square))
        """)
        self.assertEqual(reply['content']['status'], 'ok')
        self.assertIn("gradient of square at 5 is 10.0", output_msgs[0]['content']['text'])

    def test_error_runtime(self):
        reply, output_msgs = self.execute_helper(code="""
            func a() { fatalError("oops") }
        """)
        self.assertEqual(reply['content']['status'], 'ok')
        a_cell = reply['content']['execution_count']
        reply, output_msgs = self.execute_helper(code="""
            print("hello")
            print("world")
            func b() { a() }
        """)
        self.assertEqual(reply['content']['status'], 'ok')
        b_cell = reply['content']['execution_count']
        reply, output_msgs = self.execute_helper(code="""
            b()
        """)
        self.assertEqual(reply['content']['status'], 'error')
        call_cell = reply['content']['execution_count']

        stdout = output_msgs[0]['content']['text']
        self.assertIn('Fatal error: oops', stdout)
        traceback = output_msgs[1]['content']['traceback']
        all_tracebacks = '\n'.join(traceback)
        self.assertIn('Current stack trace:', all_tracebacks)
        self.assertIn('a()', all_tracebacks)
        # TODO(TF-495): Reenable these assertions.
        # self.assertIn('b() at <Cell %d>:4:24' % b_cell, all_tracebacks)
        # self.assertIn('main at <Cell %d>:2:13' % call_cell, all_tracebacks)

    def test_interrupt_execution(self):
        # Execute something to trigger debugger initialization, so that the
        # next cell executes quickly.
        self.execute_helper(code='1 + 1')

        msg_id = self.kc.execute(code="""while true {}""")

        # Give the kernel some time to actually start execution, because it
        # ignores interrupts that arrive when it's not actually executing.
        time.sleep(1)

        msg = self.kc.iopub_channel.get_msg(timeout=1)
        self.assertEqual(msg['content']['execution_state'], 'busy')

        self.km.interrupt_kernel()
        reply = self.kc.get_shell_msg(timeout=1)
        self.assertEqual(reply['content']['status'], 'error')

        while True:
            msg = self.kc.iopub_channel.get_msg(timeout=1)
            if msg['msg_type'] == 'status':
                self.assertEqual(msg['content']['execution_state'], 'idle')
                break

        # Check that the kernel can still execute things after handling an
        # interrupt.
        reply, output_msgs = self.execute_helper(
            code="""print("Hello world")""")
        self.assertEqual(reply['content']['status'], 'ok')
        for msg in output_msgs:
            if msg['msg_type'] == 'stream' and \
                    msg['content']['name'] == 'stdout':
                self.assertIn('Hello world', msg['content']['text'])
                break

    def test_async_stdout(self):
        # Execute something to trigger debugger initialization, so that the
        # next cell executes quickly.
        self.execute_helper(code='1 + 1')

        # Test that we receive stdout while execution is happening by printing
        # something and then entering an infinite loop.
        msg_id = self.kc.execute(code="""
            print("some stdout")
            while true {}
        """)

        # Give the kernel some time to send out the stdout.
        time.sleep(1)

        # Check that the kernel has sent out the stdout.
        while True:
            msg = self.kc.iopub_channel.get_msg(timeout=1)
            if msg['msg_type'] == 'stream' and \
                    msg['content']['name'] == 'stdout':
                self.assertIn('some stdout', msg['content']['text'])
                break

        # Interrupt execution and consume all messages, so that subsequent
        # tests can run. (All the tests in this class run against the same
        # instance of the kernel.)
        self.km.interrupt_kernel()
        self.kc.get_shell_msg(timeout=1)
        while True:
            msg = self.kc.iopub_channel.get_msg(timeout=1)
            if msg['msg_type'] == 'status':
                break

    def test_swift_completion(self):
        reply, output_msgs = self.execute_helper(code="""
            func aFunctionToComplete() {}
        """)
        self.assertEqual(reply['content']['status'], 'ok')

        self.kc.complete('aFunctionToC')
        reply = self.kc.get_shell_msg()
        self.assertEqual(reply['content']['matches'],
                         ['aFunctionToComplete()'])
        self.flush_channels()

        reply, output_msgs = self.execute_helper(code="""
            %disableCompletion
        """)
        self.assertEqual(reply['content']['status'], 'ok')

        self.kc.complete('aFunctionToC')
        reply = self.kc.get_shell_msg()
        self.assertEqual(reply['content']['matches'], [])
        self.flush_channels()

        reply, output_msgs = self.execute_helper(code="""
            %enableCompletion
        """)
        self.assertEqual(reply['content']['status'], 'ok')

        self.kc.complete('aFunctionToC')
        reply = self.kc.get_shell_msg()
        self.assertEqual(reply['content']['matches'],
                         ['aFunctionToComplete()'])
        self.flush_channels()

    def test_swift_clear_output(self):
        reply, output_msgs = self.execute_helper(code=r"""
            print("before the clear")
            print("\u{001B}[2J")
            print("after the clear")
        """)
        self.assertEqual(reply['content']['status'], 'ok')
        self.assertEqual(
            dict((k, output_msgs[0][k]) for k in ['msg_type', 'content']),
            {
                'msg_type': 'stream',
                'content': {
                    'name': 'stdout',
                    'text': 'before the clear\r\n',
                },
            })
        self.assertEqual(
            dict((k, output_msgs[1][k]) for k in ['msg_type', 'content']),
            {
                'msg_type': 'clear_output',
                'content': {
                    'wait': False
                }
            })
        self.assertEqual(
            dict((k, output_msgs[2][k]) for k in ['msg_type', 'content']),
            {
                'msg_type': 'stream',
                'content': {
                    'name': 'stdout',
                    'text': '\r\nafter the clear\r\n',
                },
            })


class SwiftKernelTestsPython27(SwiftKernelTestsBase,
                               jupyter_kernel_test.KernelTests):
    kernel_name = 'swift-with-python-2.7'


class SwiftKernelTests(SwiftKernelTestsBase,
                       jupyter_kernel_test.KernelTests):
    kernel_name = 'swift'


# Class for tests that need their own kernel. (`SwiftKernelTestsBase` uses one
# kernel for all the tests.)
class OwnKernelTests(unittest.TestCase):
    def test_process_killed(self):
        km, kc = start_new_kernel(kernel_name='swift')
        kc.execute("""
            import Glibc
            exit(0)
        """)
        messages = self.wait_for_idle(kc)

        had_error = False
        for message in messages:
            if message['header']['msg_type'] == 'error':
                had_error = True
                self.assertEqual(['Process killed'],
                                 message['content']['traceback'])
        self.assertTrue(had_error)

    def test_install_after_execute(self):
        # The kernel is supposed to refuse to install package after executing
        # code.

        km, kc = start_new_kernel(kernel_name='swift')
        kc.execute('1 + 1')
        self.wait_for_idle(kc)
        kc.execute("""
            %install DummyPackage DummyPackage
        """)
        messages = self.wait_for_idle(kc)

        had_error = False
        for message in messages:
            if message['header']['msg_type'] == 'error':
                had_error = True
                self.assertIn('Install Error: Packages can only be installed '
                              'during the first cell execution.',
                              message['content']['traceback'][0])
        self.assertTrue(had_error)

    def test_install_after_execute_blank(self):
        # If the user executes blank code, the kernel is supposed to try
        # to install packages. In particular, Colab sends a blank execution
        # request to the kernel when it starts up, and it's important that this
        # doesn't block package installation.

        km, kc = start_new_kernel(kernel_name='swift')
        kc.execute('\n\n\n')
        self.wait_for_idle(kc)
        kc.execute("""
            %install DummyPackage DummyPackage
        """)
        messages = self.wait_for_idle(kc)

        # DummyPackage doesn't exist, so package installation won't actually
        # succeed. So we just assert that the kernel tries to install it.
        stdout = ''
        for message in messages:
            if message['header']['msg_type'] == 'stream' and \
                    message['content']['name'] == 'stdout':
                stdout += message['content']['text']
        self.assertIn('Installing packages:', stdout)

    def wait_for_idle(self, kc):
        messages = []
        while True:
            message = kc.get_iopub_msg(timeout=30)
            messages.append(message)
            if message['header']['msg_type'] == 'status' and \
                    message['content']['execution_state'] == 'idle':
                break
        return messages
