#!/bin/bash

set -exuo pipefail

sudo apt-get install -y docker.io
gcloud auth list
gcloud beta auth configure-docker

# Sets 'swift_tf_url' to the public url corresponding to
# 'swift_tf_bigstore_gfile', if it exists.
if [[ ! -z ${swift_tf_bigstore_gfile+x} ]]; then
  export swift_tf_url="${swift_tf_bigstore_gfile/\/bigstore/https://storage.googleapis.com}"
  case "$swift_tf_url" in
    *stock*) export TENSORFLOW_USE_STANDARD_TOOLCHAIN=YES ;;
    *) export TENSORFLOW_USE_STANDARD_TOOLCHAIN=NO ;;
  esac
else
  export TENSORFLOW_USE_STANDARD_TOOLCHAIN=NO
fi

# Help debug the job's disk space.
df -h

# Move docker images into /tmpfs, where there is more space.
sudo /etc/init.d/docker stop
sudo mv /var/lib/docker /tmpfs/
sudo ln -s /tmpfs/docker /var/lib/docker
sudo /etc/init.d/docker start

# Help debug the job's disk space.
df -h

# Run tests
cd github/swift-jupyter
sudo -E docker build -t build-img -f docker/Dockerfile --build-arg swift_tf_url .
sudo docker run -p 8888:8888 --privileged build-img bash -c \
  "TENSORFLOW_USE_STANDARD_TOOLCHAIN=$TENSORFLOW_USE_STANDARD_TOOLCHAIN python3 /swift-jupyter/test/all_test.py -v"

# Create a tar artifact for kokoro to export
cd ..
tar -czf swift-jupyter.tar.gz swift-jupyter
