from setuptools import setup, find_packages

setup(
    name = "wubloader-buscribe",
    version = "0.0.0",
    packages = find_packages(),
    install_requires = [
        "argh",
        "psycopg2",
        #"gevent==1.5a2",
	"gevent",
        #"greenlet==0.4.16",
	"greenlet",
        "psycogreen",
        "wubloader-common",
        "python-dateutil",
        "vosk"
    ],
)
