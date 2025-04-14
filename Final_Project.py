import requests
from datetime import datetime, timedelta
import json
import ast
import logging
import os
import psycopg2
from collections import Counter
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from password import Password as access
import ssl
import smtplib
from email.message import EmailMessage

# Формируем имя файла с логами
current_date = datetime.now().strftime('%Y%m%d.log')
dest_folder = 'logs'

# Создаем папку для логов (если не существует)
os.makedirs(dest_folder, exist_ok=True)

# Полный путь к файлу логов
log_file_path = os.path.join(dest_folder, current_date)

# Явная настройка логгера
logger = logging.getLogger()
logger.handlers.clear()

# Создаем и настраиваем файловый обработчик
file_handler = logging.FileHandler(log_file_path, mode='a')
file_handler.setLevel(logging.INFO)  # Уровень для файла
file_handler.setFormatter(
    logging.Formatter('%(asctime)s - %(levelname)s - %(message)s', '%Y-%m-%d %H:%M:%S')
)

# Проверка доступности файла
try:
    with open(log_file_path, 'a'):
        pass
    print('Файл доступен для записи логов.')
except IOError as err:
    print(f"Ошибка доступа к файлу: {err}")

# Добавляем обработчик к логгеру
logger.addHandler(file_handler)
logger.setLevel(logging.INFO)

# Удаление файлов через через три дня
day_for_drop = datetime.strptime(current_date, '%Y%m%d.log') - timedelta(days=2)
content = os.listdir(dest_folder)
for file in content:
    try:
        file_path = os.path.join(dest_folder, file)  # Полный путь к файлу
        file_date = datetime.strptime(file, '%Y%m%d.log')
        if file_date < day_for_drop:
            os.remove(file_path)
            print(f'Файл {file} удален из папки.')
    except ValueError as err:
        print(f'Файл {file} имеет неверный формат имени: {err}')
    except FileNotFoundError as err:
        print(f'{err}. Файл {file_path} не найден.')
    except PermissionError:
        print('Нет прав на удаление')
    except Exception as err:
        print(f'Произошла ошибка: {err}')


class Extraction:
    """
    Класс для импорта статистики решения задач с API
    """
    URL_STATISTICS = 'https://b2b.itresume.ru/api/statistics'
    BASE_PARAMS = {
      'client': access.client,
      'client_key': access.client_key
    }

    def __init__(self) -> None:
        self.start = '2023-04-01 00:00:57.860798'
        self.interval = timedelta(hours=24)
        self._params_gen = self.__get_params()

    @property
    def get_start(self):
        return self.start

    @property
    def get_interval(self):
        return self.interval

    def __get_params(self):
        while True:
            # Создаём новый словарь на основе BASE_PARAMS
            params = self.BASE_PARAMS.copy()
            end = datetime.strftime(
                datetime.strptime(self.start, '%Y-%m-%d %H:%M:%S.%f') + self.interval,
                '%Y-%m-%d %H:%M:%S.%f'
            )
            params['start'] = self.start
            params['end'] = end
            yield params
            # Обновляем start для следующей итерации
            self.start = end

    def get_data(self):
        logging.info(f'Выгружаем данные с {Extraction.URL_STATISTICS}.')
        params = next(self._params_gen)
        try:
            r = requests.get(
            Extraction.URL_STATISTICS,
            params=params
            )
            r.raise_for_status()
            logging.info(f'Данные выгружены.')
            return r.json()
        except requests.exceptions.HTTPError as err:
            logging.error(f'HTTPError: {err }', exc_info=True)
            return(f'HTTP Error: {err}')
        except requests.exceptions.RequestException as err:
            logging.error(f'Request Error: {err}', exc_info=True)
            return(f'Request Error: {err}')


class Transformation:
    """
    Класс для преобразования списка статистик.
    """
    def __init__(self) -> None:
        self.passback_params = {
            'oauth_consumer_key': '',
            'lis_result_sourcedid': '',
            'lis_outcome_service_url': ''
        }

    @staticmethod
    def __prepare_passback_params(passback_params_str: str):
        """Преобразует строку passback_params в словарь."""
        if isinstance(passback_params_str, dict):
            return passback_params_str
        logging.info(f'Преобразуем строку {passback_params_str} в словарь.')
        try:
            # Сначала пробуем как JSON (если кавычки двойные)
            logging.info('Строка преобразована.')
            return json.loads(passback_params_str)
        except json.JSONDecodeError:
            try:
                # Пробуем как Python-словарь (если кавычки одинарные)
                logging.info('Строка преобразована.')
                return ast.literal_eval(passback_params_str)
            except (SyntaxError, ValueError) as err:
                # Возвращаем пустой словарь в случае ошибки
                logging.error(
                    f'{err}. Строка не преобразована. Словарь пустой.',
                    exc_info=True
                    )
                return {
                    'oauth_consumer_key': '',
                    'lis_result_sourcedid': '',
                    'lis_outcome_service_url': ''}

    @staticmethod
    def __validated_data(attemps: dict, passback_params: dict):
        """
        Проверяет данные.

        Args:
        attemps (dict): словарь пользовательской статистики.
        passback_params (dict): словарь параметров обратной передачи.
        """
        # Заполнение незаполненных passback_params
        passback_params['lis_result_sourcedid'] = passback_params.get(
            'lis_result_sourcedid', None
            )
        passback_params['lis_outcome_service_url'] = passback_params.get(
            'lis_outcome_service_url', None
            )

        # Проверка строковых типов:
        # строковых и заполненных
        if any(
            (
                not isinstance(attemps['lti_user_id'], str),
                not isinstance(attemps['attempt_type'], str),
                not isinstance(attemps['created_at'], str),
                not isinstance(passback_params['oauth_consumer_key'], str)
                )
            ):
            raise Exception('Неверный тип данных.')
        # строковых или пустых
        elif (not isinstance(
                    passback_params['lis_outcome_service_url'], (str, type(None))
                    )) or (
                        not isinstance(
                    attemps['is_correct'], (int, type(None))
            )) or (
                not isinstance(
                    passback_params['lis_result_sourcedid'], (str, type(None))
                )
            ):
            raise Exception('Неверный тип данных решения задач (is_correct).')

        # Проверка даты
        try:
            datetime.strptime(attemps['created_at'], '%Y-%m-%d %H:%M:%S.%f')
        except ValueError:
            raise ValueError('Неверный формат даты.')

        return True

    def get_statistics(self, statistics: list) -> dict:
        """
        Возвращает словарь статистики.

        Args:
        statistics (list): список статистик решения задач.

        Returns:
        dict: преобразованный список статистик:
        user_id - id пользователя,
        oauth_consumer_key - аутинфитикатор пользователя,
        lis_result_sourcedid - идентификатор, связывающий попытку пользователя с задачей,
        lis_outcome_service_url - URL для отправки оценки,
        is_correct - флаг успешного решения задачи,
        attempt_type - тип решения (отладка или отправка на проверку),
        created_at - время решения задачи.
    """
        if not statistics:
            raise Exception('Нет статистики.')

        result = []
        for statistic in statistics:
            passbaks_params = self.__prepare_passback_params(
                statistic['passback_params']
                )
            if not self.__validated_data(statistic, passbaks_params):
                continue  # Пропускаем невалидные записи

            result.append({
                'user_id': statistic['lti_user_id'],
                'oauth_consumer_key': passbaks_params['oauth_consumer_key'],
                'lis_result_sourcedid': passbaks_params['lis_result_sourcedid'],
                'lis_outcome_service_url': passbaks_params['lis_outcome_service_url'],
                'is_correct': statistic['is_correct'],
                'attempt_type': statistic['attempt_type'],
                'created_at': datetime.strptime(
                    statistic['created_at'], '%Y-%m-%d %H:%M:%S.%f'
                )
            })
            logging.info(f'Добавлена запись user_id {statistic["lti_user_id"]}')

        return result


class Analysis:
    """
    Класс вычисления статистики решения задач за день
    и выгрузки статистики в гугл-таблицу.
    """
    def __init__(self, data) -> None:
        self.data = data

    def __calculation_cnt_attempts(self):
        """
        Вычисляет статистику за отчетный день.

        Args:
        data (list): список статистик решения задач.

        Returns:
        dict: итоговый список статистик:
        date - отчетная дата,
        cnt_attemps - количество попыток,
        min_time - время первого входа на платформу,
        max_time - время последнего входа на платформу,
        most_popular_hour - час самых активных пользовательских сессий,
        cnt_correct - количество успешных решений,
        cnt_users - количество студентов, решавших задачи.
        """
        if not self.data:
            return {}

        hours = list()
        for user in self.data:
            user['hour'] = user.get('hour', user['created_at'].hour)
            hours.append(user['hour'])

        counter = Counter(list(hours))

        most_popular_hour = counter.most_common(1)[0]
        current_date = (self.data[0]['created_at']).date()
        min_time = datetime.strftime(
            min(
                self.data,
                key=lambda x: x['created_at']
                )['created_at'],
                '%Y-%m-%d %H:%M:%S'
            )
        max_time = datetime.strftime(
            max(
                self.data,
                key=lambda x: x['created_at']
                )['created_at'],
                '%Y-%m-%d %H:%M:%S'
            )
        correct_solution = list(filter(lambda x: x['is_correct'] == 1, self.data))
        cnt_users = set([user['user_id'] for user in res])
        result = {
            'date': current_date,
            'cnt_attemps': len(self.data),
            'min_time': min_time,
            'max_time': max_time,
            'most_popular_hour': most_popular_hour,
            'cnt_correct': len(correct_solution),
            'cnt_users': len(set(cnt_users))
            }

        return result

    def load_table(self):
        dt = self.__calculation_cnt_attempts()
        # Преобразуем словарь в список списков
        data_for_sheets = [[k, str(v)] for k, v in dt.items()]  # str(v) на случай datetime
        scope = [
            'https://www.googleapis.com/auth/spreadsheets',
            'https://www.googleapis.com/auth/drive'
        ]
        creds = ServiceAccountCredentials.from_json_keyfile_name(
            access.json_api_google, scope
        )
        client = gspread.authorize(creds)
        sheet = client.open_by_key(access.id_key_api_google).sheet1
        try:
            sheet.update(values=data_for_sheets, range_name="A1")
            logging.info('Данные успешно загружены в Google Sheets')
        except Exception as err:
            logging.error(f'Ошибка при загрузке в Google Sheets: {err}')
            raise


class Database:
    """
    Класс для экспорта данных о продажах в PostgreSQL (Singlton)
    """
    HOST = access.host_sql
    PORT = 5432
    DATABASE = access.base_sql
    USER = access.user_sql
    PASSWORD = access.password_sql

    def __new__(cls, *args, **kwargs):
        if not hasattr(cls, 'instance'):
            cls.instance = super().__new__(cls)
        return cls.instance

    def __init__(self, autocommit=False):
        try:
            self.connection = psycopg2.connect(
                host=Database.HOST,
                port=Database.PORT,
                database=Database.DATABASE,
                user=Database.USER,
                password=Database.PASSWORD,
                client_encoding='UTF8'
            )

            if autocommit:
                self.connection.autocommit = True

            self.cursor = self.connection.cursor()
            self.primary_key = 0
            self._primary_key_gen = self.__get_primary_key()
        except Exception as err:
            logging.error(f'Ошибка инициализации БД: {err}')
            raise

    def __get_primary_key(self):
        while True:
            # Присваиваем значение первичного ключа
            yield self.primary_key
            # Обновляем значение первичного ключа для следующей итерации
            self.primary_key += 1

    def ensure_utf8(self, value):
        if isinstance(value, str):
            return value.encode('utf-8', 'ignore').decode('utf-8')
        elif isinstance(value, bytes):
            return value.decode('utf-8', 'ignore')
        return value

    def select(self, query, vars):
        self.cursor.execute(query, vars)
        res = self.cursor.fetchall()
        return res

    def post(self, data: list):
        if not data:
            logging.warning('Нет данных для загрузки.')
            return

        logging.info(f'Начало заполнения базы {Database.DATABASE}')

        try:
            for user in data:
                # Подготовка данных
                id = next(self._primary_key_gen)
                user['id'] = user.get('id', id)
                columns = ', '.join(user.keys())
                placeholders = ', '.join(['%s'] * len(user))
                values = [self.ensure_utf8(v) for v in user.values()]

                # Формирование и выполнение запроса
                query = f'INSERT INTO solvings ({columns}) VALUES ({placeholders})'
                self.cursor.execute(query, values)

                if not self.connection.autocommit:
                    self.connection.commit()

            logging.info('Заполнение базы завершено.')
        except Exception as err:
            logging.error(f'Ошибка при загрузке данных: {err}')
            self.connection.rollback()
            raise


extractor = Extraction()
dt = extractor.get_data()
transformation = Transformation()
res = transformation.get_statistics(dt)
data = Database()
data.post(res)
analisys = Analysis(res)
analisys.load_table()

# Настройки SMTP-сервера
SMTP_SERVER = 'smtp.mail.ru'
SMTP_PORT = 465
EMAIL = access.email_from
PASSWORD = access.key_email

context = ssl.create_default_context()

# Создаём письмо
msg = EmailMessage()
msg['Subject'] = 'Отчет по статистике решения задач'
msg['From'] = EMAIL
msg['To'] = access.email_to
msg.set_content(f'Добрый день.\n Статистика решения задач за {datetime.now().date()} {access.url_sheet}')

try:
    with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT, context=ssl.create_default_context()) as server:
        server.login(EMAIL, PASSWORD)
        server.send_message(msg)
        print('Письмо успешно отправлено через SMTP_SSL!')
except Exception as err:
    print(f'Ошибка при отправке письма: {err}')
