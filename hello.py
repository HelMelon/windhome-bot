import asyncio
import aiohttp
import ujson
import datetime
from aiogram.client.default import DefaultBotProperties
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command, CommandStart
from aiogram.types import Message
from aiogram.utils.formatting import (
    Url, Bold, as_list, as_marked_section
)
from datetime import date
from aiogram.utils.media_group import MediaGroupBuilder

TOKEN = '6856100435:AAEBwyXdMbj41y22ORnHjhiZy-Llat1ldls'
URL = 'https://eri2.nca.by/guest/abandonedObject/'
dp = Dispatcher()

@dp.message(CommandStart())
async def command_start_handler(message: Message) -> None:
    await message.answer(f'Хочешь посмотреть заброшенные дома? Напиши /abandoned')

@dp.message(Command('abandoned'))
async def random_dog_handler(message: Message) -> None:

    async with aiohttp.ClientSession(json_serialize=ujson.dumps) as session:
        async with session.post('https://eri2.nca.by/api/guest/abandonedObject/search', json=
                                {
                                    'ateId': 9967, 
                                    'parentId': 9397,
                                    'abandonedObjectTypeId': 1, 
                                    'stateSearchCategoryId': 2, 
                                    'withoutStreets': False, 
                                    'destroyed': False, 
                                    'emergency': False, 
                                    'oneBasePrice': True,
                                    'stateTypeId': 16
                                }) as resp:
            allhouses = await resp.json()
            for val in allhouses['data']['content']:
                    album_builder = MediaGroupBuilder(
                      caption="Несколько фото объекта"
                    )
                    urltohouse = 'https://eri2.nca.by/api/guest/abandonedObject/' + str(val['id'])
                    async with session.post('https://eri2.nca.by/api/guest/abandonedObject/' + str(val['id']) + '/forView') as separatehouse:
                        houseinfo = await separatehouse.json()
                        if len(houseinfo['data']['images']) != 0:
                            for img in houseinfo['data']['images']:
                                fulllink = 'https://eri2.nca.by/api/images/abandonedObject/' + img['path']
                                album_builder.add_photo(
                                    media=fulllink
                                )
                    content = as_list(
                                as_marked_section(
                                    Bold(val['abandonedObjectType']),
                                    'Статус: ' + val['abandonedObjectStateType'],
                                    'Дата осмотра: ' + str(date.fromtimestamp(val['inspectionDate']/1000.0)),
                                    'Местонахождение: ' + val['position'],
                                    'Площадь: ' + str(round(houseinfo['data']['square'])) + ' кв.м.',
                                    'Стоимость: ' + str(round(houseinfo['data']['stateList'][0]['moneyAmount'])) + ' рублей',
                                    urltohouse,
                                    marker="✅ ",
                                )
                    )
                    await message.answer(**content.as_kwargs())
                    await message.answer_media_group(media=album_builder.build())

async def main() -> None:
    bot = Bot(token=TOKEN)
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())

   