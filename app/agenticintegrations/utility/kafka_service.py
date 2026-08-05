import logging
import traceback
from datetime import datetime

from confluent_kafka import Consumer, KafkaError, KafkaException

logger = logging.getLogger(__name__)


class KafkaService:
    __CONFLUENT_BOOTSTRAP_SERVER = "b-3.clickpost.md0leb.c4.kafka.ap-south-1.amazonaws.com:9092,b-2.clickpost.md0leb.c4.kafka.ap-south-1.amazonaws.com:9092,b-1.clickpost.md0leb.c4.kafka.ap-south-1.amazonaws.com:9092,b-4.clickpost.md0leb.c4.kafka.ap-south-1.amazonaws.com:9092"
    __CONFLUENT_KAFKA_CONSUMER_DEFAULT_SETTINGS = {
        'bootstrap.servers': __CONFLUENT_BOOTSTRAP_SERVER,
        'enable.auto.commit': False,
        'default.topic.config': {'auto.offset.reset': 'earliest'},
        'debug': 'consumer',
        'log_level': 7,
        'log.thread.name': True,
        'session.timeout.ms': 60000
    }

    @staticmethod
    def retrieve_consumer_cluster_config():
        return KafkaService.__CONFLUENT_KAFKA_CONSUMER_DEFAULT_SETTINGS

    @staticmethod
    def start_consumer(
            topic,
            group_id,
            consumer_callback,
            max_poll_interval_ms=None
    ):
        logger.info(
            "Starting Consumer on TOPIC: " +
            str(topic) + " with group ID: " + str(group_id)
        )

        settings = KafkaService.retrieve_consumer_cluster_config()
        settings['group.id'] = group_id
        settings['debug'] = 'consumer'
        if max_poll_interval_ms:
            settings['max.poll.interval.ms'] = max_poll_interval_ms

        logger.info(
            "Starting  Confluent Kafka consumer with settings: " + str(settings))

        consumer = Consumer(settings)

        if type(topic) is list:
            consumer.subscribe(topic)
        else:
            consumer.subscribe([topic])

        # Starting message consumption
        try:
            msg_count = 0
            last_commit_time = datetime.utcnow()
            last_processed_message = None
            partition_to_last_processed_message_map = {}
            while True:
                msg = consumer.poll(10)
                # Case: Message is empty
                if msg is None:
                    logger.info("Message Is Empty")

                    # Sometimes the commit condition, defined inside the non-empty
                    # message case below, evaluates to false even when there are some
                    # messages left to be committed. There could be more than one intermittent
                    # cause for this. For eg: the message count may not be a multiple of 20
                    # or the messages got processed so quickly that the time difference with
                    # last commit does not match the condition.
                    # Hence, these uncommitted messages are being forcefully committed below.
                    if msg_count > 0 and last_processed_message is not None:
                        # Perform message commit
                        logger.info(
                            "Commiting previous batch leftover messages")
                        for partition_number in partition_to_last_processed_message_map.keys():
                            message_to_commit = partition_to_last_processed_message_map[
                                partition_number]
                            logger.info("Committing To Partition: {} With Offset: {}"
                                                .format(partition_number, message_to_commit.offset()))
                            try:
                                consumer.commit(
                                    message=message_to_commit, asynchronous=False)
                            except KafkaException as e:
                                if e.args[0].code() == KafkaError.ILLEGAL_GENERATION:
                                    logger.info((f"Rebalance occurred, skipping commit: {e}"))
                                else:
                                    raise
                        partition_to_last_processed_message_map = {}

                        # Reset / update markers
                        last_commit_time = datetime.utcnow()
                        msg_count = 0
                        last_processed_message = None

                    continue

                # Case: Message is non empty and no errors were generated
                # during message consumption
                elif not msg.error():

                    # Logging latency and partition off-sets
                    # for the non empty message
                    msg_epoch_time = msg.timestamp()[1]
                    msg_timestamp = datetime.fromtimestamp(
                        msg_epoch_time / 1000)
                    logger.info(
                        'Received message: {0} With Partition: {1} and Offset: {2}'
                        .format(msg.value().decode('utf-8'), msg.partition(), msg.offset())
                    )
                    message_partition = msg.partition()
                    logger.info(
                        "Event Produced Timestamp: " + msg_timestamp.strftime("%Y-%m-%d %H:%M:%S"))
                    latency = int(
                        (datetime.utcnow() - msg_timestamp).total_seconds())
                    logger.info(
                        "Event Processing Consumer Running with Latency: " + str(latency) + " Seconds")

                    # Processing the consumed message using the passed callback
                    # with new_relic_obj.get_context_manager():
                    consumer_callback(msg.value().decode('utf-8'))

                    # Preparing commit markers
                    last_processed_message = msg
                    partition_to_last_processed_message_map[message_partition] = msg
                    msg_count += 1
                    current_time = datetime.utcnow()
                    time_diff_with_last_commit = int(
                        (current_time - last_commit_time).total_seconds())
                    logger.info("Time diff with Last Commit: {}".format(
                        str(time_diff_with_last_commit)))

                    # Checking for commit eligibility and commiting
                    if (time_diff_with_last_commit > 30) or (msg_count % 20 == 0):
                        # Perform message commit
                        logger.info("Committing Message")
                        for partition_number in partition_to_last_processed_message_map.keys():
                            message_to_commit = partition_to_last_processed_message_map[
                                partition_number]
                            logger.info("Committing To Partition: {} With Offset: {}"
                                                .format(partition_number, message_to_commit.offset()))
                            try:
                                consumer.commit(
                                    message=message_to_commit, asynchronous=False)
                            except KafkaException as e:
                                if e.args[0].code() == KafkaError.ILLEGAL_GENERATION:
                                    logger.info((f"Rebalance occurred, skipping commit: {e}"))
                                else:
                                    raise
                        partition_to_last_processed_message_map = {}
                        # Reset / update markers
                        last_commit_time = datetime.utcnow()
                        msg_count = 0
                        last_processed_message = None

                # Case: End of partition reached
                elif msg.error().code() == KafkaError._PARTITION_EOF:
                    logger.info('End of partition reached {0}/{1}'.format(msg.topic(), msg.partition()))

                # Case: Error occurred during message consumption
                else:
                    logger.error(
                        'Error occured: {0} With Partition: {1} and Offset: {2} '.format(
                            msg.error().str(), msg.partition(), msg.offset()))

        except KeyboardInterrupt:
            logger.error(traceback.format_exc())
            pass

        except Exception as e:
            logger.error("Error in Consumer: " + traceback.format_exc())

        finally:
            consumer.close()




