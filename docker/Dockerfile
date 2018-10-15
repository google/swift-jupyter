FROM ubuntu:16.04

USER root

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        ca-certificates \
        curl \
        git \
        python \
        python-dev \
        python-tk \
        clang \
        libcurl3 \
        libicu-dev \
        libpython-dev \
        libncurses5-dev \
	libxml2

RUN python --version

# Get pip
RUN curl -O https://bootstrap.pypa.io/get-pip.py && \
    python get-pip.py && \
    rm get-pip.py

# Install swift-jupyter dependencies and useful Python libraries
RUN pip --no-cache-dir install \
        jupyter \
        ipykernel \
        matplotlib \
        numpy \
        pandas

# Create a user and give it some directories
RUN groupadd -r swift && useradd -r -m -g swift swift
RUN mkdir -p /swift && chown swift:swift /swift
RUN mkdir -p /notebooks && chown swift:swift /notebooks
USER swift
WORKDIR /swift

# Download and extract S4TF
ENV SWIFT_TF_PLATFORM=ubuntu16.04
ENV SWIFT_TF_VERSION=swift-tensorflow-DEVELOPMENT-2018-10-05-a
ENV SWIFT_TF_URL=https://storage.googleapis.com/swift-tensorflow/$SWIFT_TF_PLATFORM/$SWIFT_TF_VERSION-$SWIFT_TF_PLATFORM.tar.gz
RUN echo "SWIFT_TF_URL=$SWIFT_TF_URL"
RUN curl -fSsL $SWIFT_TF_URL -o swift.tar.gz \
    && mkdir -p swift-tensorflow-toolchain/usr \
    && tar -xzf swift.tar.gz --directory=swift-tensorflow-toolchain/usr --strip-components=1 \
    && rm swift.tar.gz

# Install swift-jupyter
RUN git clone https://github.com/google/swift-jupyter.git
RUN python swift-jupyter/register.py --user --swift-toolchain swift-tensorflow-toolchain

# Jupyter has issues with being run directly:
#   https://github.com/ipython/ipython/issues/7062
# We just add a little wrapper script.
COPY run_jupyter.sh /swift

# Expose Jupyter
EXPOSE 8888

# Install tutorials
WORKDIR /notebooks
RUN git clone https://github.com/tensorflow/swift-tutorials.git

# Run jupyter on startup
CMD ["/swift/run_jupyter.sh", "--no-browser", "--ip=*", "--port=8888", "--NotebookApp.custom_display_url=http://127.0.0.1:8888"]
