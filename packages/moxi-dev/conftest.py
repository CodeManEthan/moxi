# moxi-dev's own tests run inside the kit's fence, as every package's do; pytester
# runs the fence's probes in a pytest of their own.
pytest_plugins = ["moxi_dev.testing", "pytester"]
