from setuptools import setup, find_packages
from ansiblebackup import __version__, __prog__

setup(name=__prog__,
      version=__version__,
      description="A command line tool to create a graph representing your Ansible playbook tasks and roles",
      url="https://github.com/narutay/ansible-backup",
      author="Yuichiro Naruta",
      author_email="narutay@gmail.com",
      license="GPL3",
      install_requires=['ansible>=2.4.0'],
      tests_requires=['pytest==3.2.3', 'pytest-cov==2.5.1'],
      packages=find_packages(exclude=['tests']),
      download_url="https://github.com/narutay/ansible-backup/archive/v" + __version__ + ".tar.gz",
      classifiers=[
          'Development Status :: 3 - Alpha',
          'Intended Audience :: Developers',
          'License :: OSI Approved :: MIT License',
          'Environment :: Console',
          'Topic :: Utilities',
          'Programming Language :: Python :: 3.5',
          'Programming Language :: Python :: 2.7',
      ],
      entry_points={
          'console_scripts': [
              '%s = ansiblebackup.cli:main' % __prog__
          ]
      })
