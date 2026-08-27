# Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

from ipykernel.kernelapp import IPKernelApp

from .kernel import ChatbookKernel

if __name__ == "__main__":
    IPKernelApp.launch_instance(kernel_class=ChatbookKernel)
