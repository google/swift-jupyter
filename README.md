# Swift-Jupyter

This is a Jupyter Kernel for Swift, intended to make it possible to use Juptyer
with the [Swift for Tensorflow](https://github.com/tensorflow/swift) project.

This kernel is currently very barebones and experimental.

This kernel is implemented using LLDB's Python APIs.

# Installation Instructions

Create a virtualenv and install jupyter in it.
```
virtualenv venv
. venv/bin/activate
pip2 install jupyter # Must use python2, because LLDB doesn't support python3.
```

Install a Swift toolchain ([see instructions here](https://github.com/tensorflow/swift/blob/master/Installation.md)).

Optionally [install SourceKitten](https://github.com/jpsim/SourceKitten) (this enables code completion).

Register the kernel with jupyter.
```
python2 register.py --sys-prefix --swift-toolchain <path to swift toolchain> --sourcekitten <path to sourcekitten binary>
```
(omit the `--sourcekitten <path to sourcekitten binary>` if you did not install SourceKitten.)

Now run `jupyter notebook`, and it should have a Swift kernel.

# Usage Instructions

## %include directives

`%include` directives let you include code from files. To use them, put a line
`%include "<filename>"` in your cell. The kernel will preprocess your cell and
replace the `%include` directive with the contents of the file before sending
your cell to the Swift interpreter.

`<filename>` must be relative to the directory containing `swift_kernel.py`.
We'll probably add more search paths later.
