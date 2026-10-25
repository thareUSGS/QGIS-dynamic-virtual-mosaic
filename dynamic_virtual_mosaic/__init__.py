def classFactory(iface):
    from .plugin import DynamicVirtualMosaicPlugin

    return DynamicVirtualMosaicPlugin(iface)
