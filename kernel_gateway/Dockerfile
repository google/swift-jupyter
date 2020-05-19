# Start from swift-jupyter image
FROM swift-jupyter:latest

# Install the kernel gateway.
WORKDIR /swift-jupyter
RUN python3 -m pip install --upgrade pip \
    && python3 -m pip install jupyter_kernel_gateway

# Run the kernel gateway on container start, with overrideable args
EXPOSE 9999
ENTRYPOINT ["python3", "-m", "jupyter", "kernelgateway"]
CMD ["--ip=0.0.0.0", "--port=9999"]
