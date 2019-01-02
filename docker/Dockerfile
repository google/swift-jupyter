# TODO: We should have a job that creates a S4TF base image so that
#we don't have to duplicate the installation everywhere.
FROM ubuntu:18.04

# Allows the caller to specify the toolchain to use.
ARG swift_tf_url=https://storage.googleapis.com/s4tf-kokoro-artifact-testing/prod/tensorflow/swift/toolchain-ubuntu-docker/release-ubuntu-18.04/5/20181117-115249/toolchain-tar/swift-tensorflow-DEVELOPMENT-2018-11-17-a-.tar.gz

# Install Swift deps.
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        ca-certificates \
        curl \
        git \
        python \
        python-dev \
        python-pip \
        python-setuptools \
        python-tk \
        python3 \
        python3-pip \
        python3-setuptools \
        clang \
        libcurl4-openssl-dev \
        libicu-dev \
        libpython-dev \
        libncurses5-dev \
	libxml2

# Upgrade pips
RUN pip2 install --upgrade pip
RUN pip3 install --upgrade pip

# Download and extract S4TF
WORKDIR /swift-tensorflow-toolchain
RUN curl -fSsL $swift_tf_url -o swift.tar.gz \
    && mkdir usr \
    && tar -xzf swift.tar.gz --directory=usr --strip-components=1 \
    && rm swift.tar.gz

# swift-jupyter runs in python2 because it uses lldb's python libs which only
# support python2. Therefore, install swift-jupyter's dependencies in python2:
RUN pip2 --no-cache-dir install \
        jupyter \
        ipykernel

# Install some python libraries that are useful to call from swift. Since
# swift can currently only call python2, install them in python2:
RUN pip2 --no-cache-dir install \
        matplotlib \
        numpy \
        pandas

# The tests use jupyter_kernel_test, which requires python3. So install that
# in python3.
RUN pip3 --no-cache-dir install \
        jupyter_kernel_test

# Copy the kernel into the container
WORKDIR /swift-jupyter
COPY . .

# Register the kernel with jupyter
RUN python2 register.py --user --swift-toolchain /swift-tensorflow-toolchain

# Run jupyter on startup
EXPOSE 8888
RUN mkdir /notebooks
WORKDIR /notebooks
CMD ["/swift-jupyter/docker/run_jupyter.sh", "--allow-root", "--no-browser", "--ip=0.0.0.0", "--port=8888", "--NotebookApp.custom_display_url=http://127.0.0.1:8888"]
