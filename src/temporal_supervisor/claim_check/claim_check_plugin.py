import os
import warnings

from dataclasses import replace
from temporalio.client import ClientConfig
from temporalio.converter import DataConverter, DefaultPayloadConverter
from temporalio.plugin import SimplePlugin

from common.util import str_to_bool
from temporal_supervisor.claim_check.claim_check_codec import ClaimCheckCodec

def _data_converter(converter: DataConverter | None) -> DataConverter:
    useClaimCheck = str_to_bool(os.getenv("USE_CLAIM_CHECK", "False"))
    if useClaimCheck == False:
        return converter

    print(f"converter is {converter}. Use Claim Check is {useClaimCheck}")

    default_converter_class = DataConverter.default.payload_converter_class
    if converter is not None:
        default_converter_class = converter.payload_converter_class
                        
    print(f"Initializing claim check plugin")
    return DataConverter(
        payload_converter_class=default_converter_class,
        payload_codec=ClaimCheckCodec()
    )


class ClaimCheckPlugin(SimplePlugin):
    def __init__(self):
        super().__init__( 
            name='ClaimCheckPlugin',
            data_converter=_data_converter,
        )
        
    async def connect_service_client(
        self,
        config: ConnectConfig,
        next: Callable[[ConnectConfig], Awaitable[ServiceClient]],
    ) -> temporalio.service.ServiceClient:
        """See base class."""
        return await next(config)
