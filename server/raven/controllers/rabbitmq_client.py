"""Base class for in-process RabbitMQ consumers.

The connection is asynchronous: the constructor starts it and returns, and pika's ioloop runs on a
daemon thread. Subclasses declare their topology in ``on_channel_open`` and handle deliveries in
``on_message``.
"""

import threading

import pika

from raven.extensions import db, logger, socketio


class RabbitMQClient:
    def __init__(self, context):
        self.context = context
        self.logger = logger
        self.db = db
        self.socketio = socketio
        self.online_euds = {}
        self.online_callsigns = {}
        self.exchanges = []
        self.rabbit_connection = None
        self.rabbit_channel = None
        self.iothread = None
        self.is_consuming = False

        try:
            config = self.context.app.config
            credentials = pika.PlainCredentials(
                config.get("RAVEN_RABBITMQ_USERNAME"), config.get("RAVEN_RABBITMQ_PASSWORD")
            )
            parameters = pika.ConnectionParameters(
                host=config.get("RAVEN_RABBITMQ_SERVER_ADDRESS"), credentials=credentials
            )
            self.rabbit_connection = pika.SelectConnection(
                parameters,
                on_open_callback=self.on_connection_open,
                on_open_error_callback=self._on_open_error,
            )
            self.iothread = threading.Thread(target=self.rabbit_connection.ioloop.start, daemon=True)
            self.iothread.start()
        except Exception as e:  # noqa: BLE001
            self.logger.error("Failed to connect to RabbitMQ: {}".format(e))

    def _on_open_error(self, connection, error):
        self.logger.error("Could not open RabbitMQ connection: {}".format(error))

    def on_connection_open(self, connection):
        connection.channel(on_open_callback=self.on_channel_open)
        connection.add_on_close_callback(self.on_close)

    def on_channel_open(self, channel):
        raise NotImplementedError("Subclasses must declare their topology in on_channel_open")

    def on_close(self, channel, error):
        self.logger.error("RabbitMQ connection closed: {}".format(error))

    def on_message(self, unused_channel, basic_deliver, properties, body):
        raise NotImplementedError("Subclasses must handle deliveries in on_message")
