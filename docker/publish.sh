#!/bin/bash

export PATH=$PATH:/usr/local/bin

USAGE="Usage: $0 {release|source|aliyun} \n
e.g. \n
release: build docker image and push to kineviz/kineviz-database-proxy \n
source: build frontend code \n
aliyun: build docker image and push to aliyun \n
"

CURRENTPATH=$(dirname "$0")
PROJECTPATH=$(cd "$CURRENTPATH"; cd ./.. ; pwd)
SHELLNAME=$(echo "$0" | awk -F "/" '{print $NF}' | awk -F "." '{print $1}')

#support in -s 
if [ -L "$0" ] ; then 
SHELLPATH=$(echo $(ls -l "$CURRENTPATH"  | grep "$SHELLNAME") | awk  -F "->" '{print $NF}') 
#SHELLNAME=$(echo $SHELLPATH | awk -F "/" '{print $NF}')
PROJECTPATH=$(cd "$(echo ${SHELLPATH%/*})/"; cd ./.. ; pwd)
fi

PROJECTNAME="kineviz-database-proxy" #kineviz-database-proxy
DOCKERHOST="kineviz" #registry.cn-hangzhou.aliyuncs.com/kineviz

#registry.cn-hangzhou.aliyuncs.com/kineviz/kineviz-database-proxy:release
if [ -z "$2" ]; then
    echo "Default docker registry host : $DOCKERHOST "
else
DOCKERHOST=$2
    echo "Read the docker registry host : $DOCKERHOST "
fi

source_build(){
    echo "Building frontend code..."
    cd "${PROJECTPATH}"
    
    # Install frontend dependencies if node_modules doesn't exist
    if [ ! -d "frontend/node_modules" ]; then
        echo "Installing frontend dependencies..."
        cd frontend && npm install && cd ..
    fi
    
    # Build frontend
    echo "Building frontend..."
    cd frontend && npm run build && cd ..
    
    echo "Frontend build completed!"
}

aliyun_push(){
    version="$1"
    ALIYUN_DOCKERHOST=registry.cn-hangzhou.aliyuncs.com/kineviz
    echo "Pushing docker image ${PROJECTNAME}:${version} to ${ALIYUN_DOCKERHOST}"
    docker tag  "${DOCKERHOST}/${PROJECTNAME}:${version}" "${ALIYUN_DOCKERHOST}/${PROJECTNAME}:${version}"
    docker push "${ALIYUN_DOCKERHOST}/${PROJECTNAME}:${version}"
    echo "Docker image ${ALIYUN_DOCKERHOST}/${PROJECTNAME}:${version} pushed successfully!"
}

docker_build(){
    version="$1"
    echo "Building docker image ${DOCKERHOST}/${PROJECTNAME}:${version}"
    cd "${PROJECTPATH}"
    
    # Build frontend first
    echo "Building frontend code before Docker build..."
    source_build
    
    # Check if Dockerfile exists
    if [ ! -f "${PROJECTPATH}/docker/Dockerfile" ]; then 
        echo "Can't find docker/Dockerfile file"
        exit 1
    else 
        docker buildx build \
            -f ./docker/Dockerfile \
            --platform linux/amd64,linux/arm64 \
            -t "${DOCKERHOST}/${PROJECTNAME}:${version}" ./ 
        echo "Docker image ${DOCKERHOST}/${PROJECTNAME}:${version} built successfully!"
    fi
}

docker_push(){
    version="$1"
    echo "Pushing docker image ${DOCKERHOST}/${PROJECTNAME}:${version}"
    docker push "${DOCKERHOST}/${PROJECTNAME}:${version}"
    echo "Docker image ${DOCKERHOST}/${PROJECTNAME}:${version} pushed successfully!"
}

 

run() {

  case "$1" in
     release)
     docker_build latest
     docker_push latest
        ;;
    source)
     source_build
        ;;
    aliyun)
     docker_build latest
     aliyun_push latest
        ;;
    *)
        echo "$USAGE"
     ;;
esac

exit 0;

}

if [ -z "$1" ]; then
    echo "$USAGE"
    exit 0
fi

run "$1"