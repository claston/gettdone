"""Private S3/SQS transport for the Java worker; no document goes to third parties."""
import json
import os
from uuid import UUID

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError


class ShadowTransport:
    def __init__(self):
        self.bucket = os.environ["CLASSIFICATION_SHADOW_BUCKET"]
        self.queue = os.environ["CLASSIFICATION_SHADOW_QUEUE_URL"]
        config = Config(connect_timeout=3, read_timeout=5, retries={"max_attempts": 1})
        region = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION")
        self.s3 = boto3.client("s3", region_name=region, config=config)
        self.sqs = boto3.client("sqs", region_name=region, config=config)

    @staticmethod
    def key(identifier, kind):
        return f"classification-shadow/{str(UUID(identifier))}/{kind}.json"

    def publish(self, request):
        key = self.key(request["run_id"], "request")
        try:
            self.s3.put_object(Bucket=self.bucket, Key=key, Body=json.dumps(request, allow_nan=False).encode(),
                               ContentType="application/json", ServerSideEncryption="AES256", IfNoneMatch="*")
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "PreconditionFailed":
                raise RuntimeError("Não foi possível publicar a avaliação.") from exc
        except BotoCoreError as exc:
            raise RuntimeError("Não foi possível publicar a avaliação.") from exc
        try:
            self.sqs.send_message(QueueUrl=self.queue, MessageBody=json.dumps({"schema_version": 1, "run_id": request["run_id"]}))
        except (ClientError, BotoCoreError) as exc:
            raise RuntimeError("Não foi possível enfileirar a avaliação.") from exc

    def result(self, identifier):
        try:
            response = self.s3.get_object(Bucket=self.bucket, Key=self.key(identifier, "result"))
        except ClientError as exc:
            if exc.response["Error"]["Code"] in {"NoSuchKey", "404"}:
                return None
            raise RuntimeError("Não foi possível consultar a avaliação.") from exc
        stream = response["Body"]
        try:
            body = stream.read(2 * 1024 * 1024 + 1)
        finally:
            stream.close()
        if len(body) > 2 * 1024 * 1024:
            raise ValueError("Resultado fiscal excedeu o limite.")
        return json.loads(body)
