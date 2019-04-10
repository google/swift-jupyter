# Swift-Jupyter

This is a Jupyter Kernel for Swift, intended to make it possible to use Jupyter
with the [Swift for TensorFlow](https://github.com/tensorflow/swift) project.

# Installation Instructions

## Option 1: Using a Swift for TensorFlow toolchain and Virtualenv

### Requirements

Operating system:

* Ubuntu 18.04 (64-bit); OR
* other operating systems may work, but you will have to build Swift from
  sources.

Dependencies:

* Python 3 (Ubuntu 18.04 package name: `python3`)
* Python 3 Virtualenv (Ubuntu 18.04 package name: `python3-venv`)

### Installation

swift-jupyter requires a Swift toolchain with LLDB Python3 support. Currently, the only prebuilt toolchains with LLDB Python3 support are the [Swift for TensorFlow Ubuntu 18.04 Nightly Builds](https://github.com/tensorflow/swift/blob/master/Installation.md#pre-built-packages). Alternatively, you can build a toolchain from sources (see the section below for instructions).

Extract the Swift toolchain somewhere.

Create a virtualenv, install the requirements in it, and register the kernel in
it:

```bash
python3 -m venv venv
. venv/bin/activate
pip install -r requirements.txt
python register.py --sys-prefix --swift-toolchain <path to extracted swift toolchain directory>
```

Finally, run Jupyter:

```bash
. venv/bin/activate
jupyter notebook
```

You should be able to create Swift notebooks. Installation is done!

## Option 2: Using a Swift for TensorFlow toolchain and Conda

### Requirements

Operating system:

* Ubuntu 18.04 (64-bit); OR
* other operating systems may work, but you will have to build Swift from
  sources.

### Installation

#### 1. Get toolchain

swift-jupyter requires a Swift toolchain with LLDB Python3 support. Currently, the only prebuilt toolchains with LLDB Python3 support are the [Swift for TensorFlow Ubuntu 18.04 Nightly Builds](https://github.com/tensorflow/swift/blob/master/Installation.md#pre-built-packages). Alternatively, you can build a toolchain from sources (see the section below for instructions).

Extract the Swift toolchain somewhere.

Important note about CUDA/CUDNN: If you are using a CUDA toolchain, then you should install CUDA and CUDNN on your system
without using Conda, because Conda's CUDNN is too old to work with the Swift toolchain's TensorFlow. (As of 2019-04-08,
Swift for TensorFlow requires CUDNN 7.5, but Conda only has CUDNN 7.3).

#### 2. Initialize environment

Create a Conda environment and install some packages in it:

```bash
conda create -n swift-tensorflow python==3.6
conda activate swift-tensorflow
conda install jupyter numpy matplotlib
```

#### 3. Register kernel

Register the Swift kernel with Jupyter:

```bash
python register.py --sys-prefix --swift-python-use-conda --use-conda-shared-libs \
  --swift-toolchain <path to extracted swift toolchain directory>
```

Finally, run Jupyter:

```bash
jupyter notebook
```

You should be able to create Swift notebooks. Installation is done!

## Option 3: Using the Docker Container

This repository also includes a dockerfile which can be used to run a Jupyter Notebook instance which includes this Swift kernel. To build the container, the following command may be used:

```bash
# from inside the directory of this repository
docker build -f docker/Dockerfile -t swift-jupyter .
```

The resulting container comes with the latest Swift for TensorFlow toolchain installed, along with Jupyter and the Swift kernel contained in this repository.

This container can now be run with the following command:

```bash
docker run -p 8888:8888 --cap-add SYS_PTRACE -v /my/host/notebooks:/notebooks swift-jupyter
```

The functions of these parameters are:

- `-p 8888:8888` exposes the port on which Jupyter is running to the host.

- `--cap-add SYS_PTRACE` adjusts the privileges with which this container is run, which is required for the Swift REPL.

- `-v <host path>:/notebooks` bind mounts a host directory as a volume where notebooks created in the container will be stored.  If this command is omitted, any notebooks created using the container will not be persisted when the container is stopped.

## (optional) Building toolchain with LLDB Python3 support

Follow the
[Building Swift for TensorFlow](https://github.com/apple/swift/tree/tensorflow#building-swift-for-tensorflow)
instructions, with some modifications:

* Also install the Python 3 development headers. (For Ubuntu 18.04,
  `sudo apt-get install libpython3-dev`). The LLDB build will automatically
  find these and build with Python 3 support.
* Instead of running `utils/build-script`, run
  `SWIFT_PACKAGE=tensorflow_linux,no_test ./swift/utils/build-toolchain local.swift`
  or `SWIFT_PACKAGE=tensorflow_linux ./swift/utils/build-toolchain local.swift,gpu,no_test`
  (depending on whether you want to build tensorflow with GPU support).

This will create a tar file containing the full toolchain. You can now proceed
with the installation instructions from the previous section.

# Usage Instructions

## Rich output

You can call Python libraries using [Swift's Python interop] to display rich
output in your Swift notebooks. (Eventually, we'd like to support Swift
libraries that produce rich output too!)

Prerequisites:

* You must use a Swift toolchain that has Python interop. As of February 2019,
  only the Swift for TensorFlow toolchains have Python interop.

After taking care of the prerequisites, run
`%include "EnableIPythonDisplay.swift"` in your Swift notebook. Now you should
be able to display rich output! For example:

```swift
let np = Python.import("numpy")
let plt = Python.import("matplotlib.pyplot")
IPythonDisplay.shell.enable_matplotlib("inline")
```

```swift
let time = np.arange(0, 10, 0.01)
let amplitude = np.exp(-0.1 * time)
let position = amplitude * np.sin(3 * time)

plt.figure(figsize: [15, 10])

plt.plot(time, position)
plt.plot(time, amplitude)
plt.plot(time, -amplitude)

plt.xlabel("time (s)")
plt.ylabel("position (m)")
plt.title("Oscillations")

plt.show()
```

![Screenshot of running the above two snippets of code in Jupyter](./screenshots/display_matplotlib.png)

```swift
let display = Python.import("IPython.display")
let pd = Python.import("pandas")
```

```swift
display.display(pd.DataFrame.from_records([["col 1": 3, "col 2": 5], ["col 1": 8, "col 2": 2]]))
```

![Screenshot of running the above two snippets of code in Jupyter](./screenshots/display_pandas.png)

[Swift's Python interop]: https://github.com/tensorflow/swift/blob/master/docs/PythonInteroperability.md

## %install directives

**Note: Requires a Swift for TensorFlow toolchain built on or after March 20, 2019.**

`%install` directives let you install SwiftPM packages so that your notebook
can import them:

```swift
// Specify SwiftPM flags to use during package installation.
%install-swiftpm-flags -c release

// Install the DeckOfPlayingCards package from GitHub.
%install '.package(url: "https://github.com/NSHipster/DeckOfPlayingCards", from: "4.0.0")' DeckOfPlayingCards

// Install the SimplePackage package that's in the kernel's working directory.
%install '.package(path: "$cwd/SimplePackage")' SimplePackage
```

The first argument to `%install` is a [SwiftPM package dependency specification](https://github.com/apple/swift-package-manager/blob/master/Documentation/PackageDescriptionV4.md#dependencies).
The next argument(s) to `%install` are the products that you want to install from the package.

`%install` directives currently have some limitations:

* You must install all your packages in the first cell that you execute. (It
  will refuse to install packages, and print out an error message explaining
  why, if you try to install packages in later cells.)
* Downloads and build artifacts are not cached.
* `%install-swiftpm-flags` apply to all packages that you are installing; there
  is no way to specify different flags for different packages.

## %include directives

`%include` directives let you include code from files. To use them, put a line
`%include "<filename>"` in your cell. The kernel will preprocess your cell and
replace the `%include` directive with the contents of the file before sending
your cell to the Swift interpreter.

`<filename>` must be relative to the directory containing `swift_kernel.py`.
We'll probably add more search paths later.

# Running tests

## Locally

Install swift-jupyter locally using the above installation instructions. Now
you can activate the virtualenv and run the tests:

```
. venv/bin/activate
python test/fast_test.py  # Fast tests, should complete in 1-2 min
python test/all_test_local.py  # Much slower, 10+ min
python test/all_test_local.py SimpleNotebookTests.test_simple_successful  # Invoke specific test method
```

You might also be interested in manually invoking the notebook tester on
specific notebooks. See its `--help` documentation:

```
python test/notebook_tester.py --help
```

## In Docker

After building the docker image according to the instructions above,

```
docker run --cap-add SYS_PTRACE swift-jupyter python3 /swift-jupyter/test/all_test_docker.py
```
