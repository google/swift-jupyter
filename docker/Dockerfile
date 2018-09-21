FROM gcr.io/swift-tensorflow/swift-tensorflow

USER root

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

USER swift

# Copy the kernel into the container
RUN mkdir /home/swift/jupyter
COPY . /home/swift/jupyter
WORKDIR /home/swift/jupyter

# Register the kernel with jupyter
RUN python2 register.py --user --swift-toolchain /home/swift/swift-tensorflow-toolchain

# Run jupyter on startup
EXPOSE 8888
RUN mkdir /home/swift/notebooks
WORKDIR /home/swift/notebooks
CMD ["/home/swift/jupyter/docker/run_jupyter.sh", "--no-browser", "--ip=0.0.0.0", "--port=8888", "--NotebookApp.custom_display_url=http://127.0.0.1:8888"]
