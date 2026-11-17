# Every test of this package runs inside Moxi's test fence: an environment of its own,
# no write outside its tmp and no connect off this machine. See moxi_dev.testing.
pytest_plugins = ["moxi_dev.testing"]
