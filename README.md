# BioView Data Monitor

A front-end for real-time visualization of data acquired by the BioView server

Configurations passed across client and server in JSON. Client gets config at load time - if not, then asks for config to be uploaded using popup.

JSON schema for common configs -
{
    'data_path'
}

JSON schema for device configs -
{
    'name':
    'type':
    'params': {
        // This includes necessary params for controlling device
    }
}

Each device will communicate available sources to the client after being setup. These will be used for plotting/saving/other functionality. 

Devices should have configuration params as well to be used with configurator