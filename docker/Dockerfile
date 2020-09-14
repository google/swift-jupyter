# Start from S4TF base image
FROM gcr.io/swift-tensorflow/base-deps-cuda10.2-cudnn7-ubuntu18.04

# Allow the caller to specify the toolchain to use
ARG swift_tf_url=https://storage.googleapis.com/swift-tensorflow-artifacts/nightlies/latest/swift-tensorflow-DEVELOPMENT-cuda10.2-cudnn7-ubuntu18.04.tar.gz

# Install some python libraries that are useful to call from swift
WORKDIR /swift-jupyter
COPY docker/requirements*.txt ./
RUN python3 -m pip install --upgrade pip \
    && python3 -m pip install --no-cache-dir -r requirements.txt \
    && python3 -m pip install --no-cache-dir -r requirements_py_graphics.txt

# Download and extract S4TF
WORKDIR /swift-tensorflow-toolchain
ADD $swift_tf_url swift.tar.gz
RUN mkdir usr \
    && tar -xzf swift.tar.gz --directory=usr --strip-components=1 \
    && rm swift.tar.gz

# Copy the kernel into the container
WORKDIR /swift-jupyter
COPY . .

# Register the kernel with jupyter
RUN python3 register.py --user --swift-toolchain /swift-tensorflow-toolchain

# Add Swift to the PATH
ENV PATH="$PATH:/swift-tensorflow-toolchain/usr/bin/"

# Create the notebooks dir for mounting
RUN mkdir /notebooks
WORKDIR /notebooks

# Run Jupyter on container start
EXPOSE 8888
CMD ["/swift-jupyter/docker/run_jupyter.sh", "--allow-root", "--no-browser", "--ip=0.0.0.0", "--port=8888", "--NotebookApp.custom_display_url=http://127.0.0.1:8888"]
