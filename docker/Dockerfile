# TODO: We should have a job that creates a S4TF base image so that
#we don't have to duplicate the installation everywhere.
FROM nvidia/cuda:10.0-cudnn7-devel-ubuntu18.04

# Allows the caller to specify the toolchain to use.
ARG swift_tf_url=https://storage.googleapis.com/s4tf-kokoro-artifact-testing/latest/swift-tensorflow-DEVELOPMENT-cuda10.0-cudnn7-ubuntu18.04.tar.gz

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
        libpython3-dev \
        libncurses5-dev \
        libxml2 \
        libblocksruntime-dev

# Upgrade pips
RUN pip2 install --upgrade pip
RUN pip3 install --upgrade pip

# Install swift-jupyter's dependencies in python3 because we run the kernel in python3.
WORKDIR /swift-jupyter
COPY docker/requirements*.txt ./
RUN pip3 install -r requirements.txt

# Install some python libraries that are useful to call from swift. Since
# swift can interoperate with python2 and python3, install them in both.
RUN pip2 install -r requirements_py_graphics.txt
RUN pip3 install -r requirements_py_graphics.txt

# Copy the kernel into the container
WORKDIR /swift-jupyter
COPY . .

# Download and extract S4TF
WORKDIR /swift-tensorflow-toolchain
ADD $swift_tf_url swift.tar.gz
RUN mkdir usr \
    && tar -xzf swift.tar.gz --directory=usr --strip-components=1 \
    && rm swift.tar.gz

# Register the kernel with jupyter
WORKDIR /swift-jupyter
RUN python3 register.py --user --swift-toolchain /swift-tensorflow-toolchain --swift-python-version 2.7 --kernel-name "Swift (with Python 2.7)" && \
    python3 register.py --user --swift-toolchain /swift-tensorflow-toolchain --swift-python-library /usr/lib/x86_64-linux-gnu/libpython3.6m.so --kernel-name "Swift"

# Configure cuda
RUN echo "/usr/local/cuda-10.0/targets/x86_64-linux/lib/stubs" > /etc/ld.so.conf.d/cuda-10.0-stubs.conf && \
    ldconfig

# Run jupyter on startup
EXPOSE 8888
RUN mkdir /notebooks
WORKDIR /notebooks
CMD ["/swift-jupyter/docker/run_jupyter.sh", "--allow-root", "--no-browser", "--ip=0.0.0.0", "--port=8888", "--NotebookApp.custom_display_url=http://127.0.0.1:8888"]
