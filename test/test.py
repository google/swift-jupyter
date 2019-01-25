import unittest
import jupyter_kernel_test
import sys

# This superclass defines tests but does not run them against kernels, so that
# we can subclass this to run the same tests against different kernels.
#
# In particular, the subclasses run against kernels that interop with differnt
# versions of Python, so that we test that graphics work with different versions
# of Python.
class SwiftKernelTests:
    language_name = 'swift'

    code_hello_world = 'print("hello, world!")'

    code_execute_result = [
        {'code': 'let x = 2; x', 'result': '2\n'}
    ]

    code_generate_error = 'varThatIsntDefined'

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

    def test_gradient_across_cells(self):
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
        self.assertEqual(reply['content']['status'], 'ok')
        self.assertIn("gradient of square at 5 is 10.0", output_msgs[0]['content']['text'])


class SwiftKernelTestsPython27(SwiftKernelTests,
                               jupyter_kernel_test.KernelTests):
    kernel_name = 'swift-with-python-2.7'


class SwiftKernelTestsPython36(SwiftKernelTests,
                               jupyter_kernel_test.KernelTests):
    kernel_name = 'swift-with-python-3.6'


if __name__ == '__main__':
    unittest.main()
