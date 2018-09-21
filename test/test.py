import unittest
import jupyter_kernel_test

class SwiftKernelTests(jupyter_kernel_test.KernelTests):
    kernel_name = 'swift'

    language_name = 'swift'

    code_hello_world = 'print("hello, world!")'

    code_execute_result = [
        {'code': 'let x = 2; x', 'result': '2\n'}
    ]

    code_generate_error = 'varThatIsntDefined'

if __name__ == '__main__':
    unittest.main()
