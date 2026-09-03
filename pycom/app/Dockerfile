FROM ubuntu:22.04
RUN apt update && apt install -y python3 python3-pip openjdk-17-jdk zip unzip git
RUN pip install buildozer cython==0.29.33
WORKDIR /app
COPY . /app
RUN buildozer android debug
