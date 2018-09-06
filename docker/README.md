# Docker

## Using the image from the registry

`docker run -t -i -p 8888:8888 --cap-add SYS_PTRACE gcr.io/swift-tensorflow/jupyter`

## Building an image

`docker build -t gcr.io/swift-tensorflow/jupyter .`

## Pushing an image to the registry

1. Ask marcrasi@google.com for access to the Google Cloud Project.
2. Follow the "Before you begin" instructions [here](https://cloud.google.com/container-registry/docs/pushing-and-pulling).
3. Build an image, as described in the previous section.
4. `docker push gcr.io/swift-tensorflow/jupyter`
