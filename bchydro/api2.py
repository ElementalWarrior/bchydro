#import sys
import aiohttp
import logging
# from datetime import datetime, timedelta
# import xml.etree.ElementTree as ET
# from bs4 import BeautifulSoup
# from ratelimit import limits
# from tenacity import (
#     retry,
#     stop_after_attempt,
#     retry_if_exception_type,
#     wait_fixed,
#     TryAgain,
# )

import asyncio
from playwright.async_api import async_playwright
import time
import os
import xml.etree.ElementTree as ET


from .types import (
    BCHydroAccount,
    BCHydroInterval,
    BCHydroRates,
    BCHydroDailyElectricity,
    BCHydroDailyUsage,
)

from .exceptions import (
    BCHydroAuthException,
    BCHydroParamException,
    BCHydroInvalidXmlException,
    BCHydroAlertDialogException,
    BCHydroInvalidDataException,
)

from .const import (
    FIVE_MINUTES,
    USER_AGENT,
    URL_POST_LOGIN,
    URL_LOGIN_GOTO,
    URL_GET_ACCOUNTS,
    URL_ACCOUNTS_OVERVIEW,
    URL_GET_ACCOUNT_JSON,
    URL_POST_CONSUMPTION_XML,

    URL_LOGIN_PAGE,
)

from .util import parse_consumption_xml

LOGLEVEL = os.environ.get('LOGLEVEL', 'WARNING').upper()
#logging.basicConfig(level=LOGLEVEL)
logger = logging.getLogger(__name__)
logger.setLevel(LOGLEVEL)


class BCHydroApi2:
    def __init__(self, username, password):
        self.username = username
        self.password = password
        self.slid = None
        self.accountNumber = None
        
    def _authenticated(func):
        async def wrapper(self, *args, **kwargs):
            # if not (self.slid and self.page):
            await self.authenticate()
            return await func(self, *args, **kwargs)
        return wrapper

    async def authenticate(self):
        p = await async_playwright().__aenter__()
        logger.debug('Launching firefox...')
        self.browser = await p.firefox.launch()
        self.page = await self.browser.new_page()

        logger.debug('Populating login form...')
        await self.page.goto(URL_LOGIN_PAGE)
        await self.page.locator('#email').wait_for()
        await self.page.type('#email', self.username)
        await self.page.type('#password', self.password)

        logger.debug('Clicking login button...')
        await asyncio.gather(
            # self.page.waitForNavigation(),
            self.page.click('#submit-button'),
        )

        await self.page.locator("#billing_widget").wait_for()
        logger.debug('Extracting account numbers...')
        self.slid = await self.page.evaluate("window.input_slid")
        self.accountNumber = await self.page.evaluate("window.input_accountNumber")
        print(self.accountNumber)
        print(self.slid)

    @_authenticated
    async def get_accounts(self):
        eval_js = f"""
        async()=>{{
            const res = await fetch(
                '{url}',
                {{
                    method: 'POST',
                    headers: {{
                        'Content-Type':'application/x-www-form-urlencoded',
                        'bchydroparam':'{bchp}',
                        'x-csrf-token':'{bchp}',
                    }}
                }}
            );
            const text = await res.text();
            return text;
        }}
        """

    @_authenticated
    async def get_usage(self, hourly=False):
        # Navigate to Consumption page by clicking button:
        # await self.page.wait_for_load_state()
        # await self.page.locator('#detailCon:not([disabled])').wait_for()
        # await asyncio.gather(
        #     self.page.locator("#detailCon").wait_for(),
        #     self.page.click('#detailCon'),
        # )
        # logger.debug('waiting Detailed Consumption button...')
        # await self.page.locator("#detailCon:not([disabled])").wait_for()
        logger.debug('Clicking Detailed Consumption button...')
        await self.page.click('#detailCon')
        await self.page.wait_for_timeout(1000)

        # Evaluate JS fetch() request in DOM
        logger.debug('Extracting bchydroparam...')
        span = self.page.locator("span#bchydroparam")
        bchp = await span.evaluate("span => span.innerText")
        # bchp = await self.page.evaluate("document.querySelector('span#bchydroparam').innerText")
        url="https://app.bchydro.com/evportlet/web/consumption-data.html"

        logger.debug('Making fetch() request...')

        evpBillingStart = await self.page.evaluate("window.g_billingStartDateTime.toXmlDate()")
        evpBillingEnd = await self.page.evaluate("window.g_billingEndDateTime.toXmlDate()")
        postdata = f'Slid={self.slid}&Account={self.accountNumber}&ChartType=column&Granularity=daily&Overlays=none&StartDateTime={evpBillingStart}&EndDateTime={evpBillingEnd}&DateRange=currentBill&RateGroup=RES1'
        eval_js = f"""
        async()=>{{
            const res = await fetch(
                '{url}',
                {{
                    method: 'POST',
                    headers: {{
                        'Content-Type':'application/x-www-form-urlencoded',
                        'bchydroparam':'{bchp}',
                        'x-csrf-token':'{bchp}',
                    }},
                    body: '{postdata}'
                }}
            );
            const text = await res.text();
            return text;
        }}
        """

        xml = await self.page.evaluate(eval_js)
        usage, rates = parse_consumption_xml(xml)

        self.usage = usage
        # self._set_latest_point(usage)
        self._set_latest_interval(usage)
        self._set_latest_usage(usage)
        self._set_latest_cost(usage)
        return usage, rates

    def _is_valid_point(self, point):
        return point.quality == "ACTUAL"

    def _set_latest_point(self, usage):
        valid_point = list(filter(self._is_valid_point, self.usage.electricity))
        self.latest_point = valid_point[-1] if len(valid_point) else None

    async def get_latest_point(self) -> BCHydroDailyElectricity:
        return self.latest_point

    def _set_latest_interval(self, usage):
        valid_point = list(filter(self._is_valid_point, self.usage))
        self.latest_interval = valid_point[-1].interval if len(valid_point) else None

    async def get_latest_interval(self) -> BCHydroInterval:
        return self.latest_interval

    def _set_latest_usage(self, usage):
        valid_point = list(filter(self._is_valid_point, self.usage))
        self.latest_usage = valid_point[-1].consumption if len(valid_point) else None

    async def get_latest_usage(self):
        return self.latest_usage

    def _set_latest_cost(self, usage):
        valid_point = list(filter(self._is_valid_point, self.usage))
        self.latest_cost = valid_point[-1].cost if len(valid_point) else None

    async def get_latest_cost(self):
        return self.latest_cost
