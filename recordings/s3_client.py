import logging
from django.conf import settings
import boto3
from botocore.config import Config

logger = logging.getLogger(__name__)


def get_s3_client():
    return boto3.client(
        's3',
        endpoint_url=settings.S3_ENDPOINT_URL,
        aws_access_key_id=settings.S3_ACCESS_KEY,
        aws_secret_access_key=settings.S3_SECRET_KEY,
        config=Config(
            signature_version='s3v4',
            s3={'addressing_style': 'path'},
        ),
        use_ssl=settings.S3_USE_SSL,
        verify=settings.S3_VERIFY_SSL,
    )


def list_mp3_files():
    """List all .mp3 objects in the bucket, optionally under prefix."""
    client = get_s3_client()
    bucket = settings.S3_BUCKET
    prefix = (settings.S3_PREFIX or '').strip()
    if prefix and not prefix.endswith('/'):
        prefix += '/'
    result = []
    paginator = client.get_paginator('list_objects_v2')
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix or ''):
        for obj in page.get('Contents') or []:
            key = obj['Key']
            if key.endswith('.mp3'):
                result.append({
                    'key': key,
                    'size': obj['Size'],
                    'last_modified': obj['LastModified'],
                })
    return result


def download_mp3_to_local(s3_key, local_path):
    """Download one file from S3 to local path."""
    client = get_s3_client()
    client.download_file(settings.S3_BUCKET, s3_key, str(local_path))


def upload_file_to_s3(local_path, s3_key, content_type='audio/mpeg'):
    """Загрузить файл на S3. content_type для MP3 — audio/mpeg."""
    client = get_s3_client()
    with open(local_path, 'rb') as f:
        client.upload_fileobj(
            f,
            settings.S3_BUCKET,
            s3_key,
            ExtraArgs={'ContentType': content_type},
        )


def get_presigned_download_url(s3_key, filename, expires_in=60):
    """Возвращает временную ссылку на скачивание файла из S3."""
    client = get_s3_client()
    return client.generate_presigned_url(
        'get_object',
        Params={'Bucket': settings.S3_BUCKET, 'Key': s3_key, 'ResponseContentDisposition': f'attachment; filename="{filename}"'},
        ExpiresIn=expires_in,
    )


def delete_mp3_from_s3(s3_key):
    """Удалить файл из S3 по ключу. Возвращает True при успехе."""
    try:
        client = get_s3_client()
        client.delete_object(Bucket=settings.S3_BUCKET, Key=s3_key)
        return True
    except Exception as e:
        logger.exception('S3 delete failed for %s: %s', s3_key, e)
        return False
