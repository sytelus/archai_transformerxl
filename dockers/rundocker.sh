docker run --gpus all --name archai \
    --rm \
    -u $(id -u):$(id -g) \
    -e HOME=$HOME -e USER=$USER \
    -e NCCL_P2P_LEVEL=NVL \
    -v $HOME:$HOME \
    -v /dataroot:$HOME/dataroot \
    -w $HOME \
    --shm-size=10g \
    --ulimit memlock=-1 \
    --net=host \
    -it sytelus/archai