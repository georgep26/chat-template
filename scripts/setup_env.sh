#!/bin/bash

conda env create -f environment.yml || conda env update -f environment.yml
