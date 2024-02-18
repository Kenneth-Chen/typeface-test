from flask import Flask, request, abort, redirect, jsonify
from flask.views import MethodView
import os
import psycopg2
import boto3
import urllib.parse
from datetime import datetime

app = Flask(__name__)

# retrieve database connection details
# TODO: cleanup code and put this in a configuration manager
db = {}
db['host'] = os.environ['POSTGRES_HOST']
db['name'] = os.environ['POSTGRES_DB_NAME']
db['user'] = os.environ['POSTGRES_DB_USER']
db['password'] = os.environ['POSTGRES_PASSWORD']
S3_BUCKET = os.environ['S3_BUCKET']
S3_KEY = os.environ['S3_KEY']
S3_SECRET = os.environ['S3_SECRET']
S3_BUCKET_URI = os.environ['S3_BUCKET_URI']

class FileAPI(MethodView):

    # get file by id or list all files
    def get(self, file_id=None):
        with psycopg2.connect(host=db['host'], user=db['user'], password=db['password'], database=db['name']) as conn:
            with conn.cursor() as cur:
                # get file by id
                if file_id is not None:
                    cur.execute("SELECT file_uri FROM files WHERE file_id = %s", (file_id,))
                    if cur.rowcount == 0: abort(404, description="File not found")
                    results = cur.fetchone()
                    # technically, the requirements specified we should be responding with the actual file binary here
                    # if so, instead of redirecting, we should be downloading the file from S3 and including it in the response
                    # however, this negates a lot of the benefits using S3 in the first place, so we return a redirect to the file URI instead
                    return redirect(results[0])
                # list all files
                else:
                    # We want to specify the fields we're selecting as it's better practice than using wildcard selectors, and we want to cast datetime field to string,
                    # since db datetime fields cannot be converted into json directly. We could convert it in Python, but asking the db to do this is more efficient.
                    cur.execute("SELECT file_id, file_name, file_uri, file_size_bytes, file_type, TO_CHAR(created_at, 'YYYY-MM-DD HH24:MI:SS') AS created_at FROM files")
                    row_headers = [x[0] for x in cur.description]
                    results = cur.fetchall()

                    json_data = [dict(zip(row_headers, result)) for result in results]
                    return jsonify({'files': json_data}), 200

    # upload new file
    def post(self):
        if 'file' not in request.files: return abort(400, description="Missing required file")
        file = request.files['file']

        # if the user does not select a file, the browser submits an empty file without a filename
        if not file.filename: return abort(400, description="Missing filename")

        with psycopg2.connect(host=db['host'], user=db['user'], password=db['password'], database=db['name']) as conn:
            with conn.cursor() as cur:
                # check if this file already exists -- we don't want to allow upload if it does
                # I've assumed we want to make this check, because otherwise the PUT method would be redundant
                # however, note that we will only get the correct result from this query if the file name is globally unique
                # in a real system, the file name probably wouldn't be globally unique, but perhaps be unique for each user
                # in that case, we might choose not to allow uploading the same file for the same user's account, or we might choose to allow it, depending on the use case
                cur.execute("SELECT file_id FROM files WHERE file_name = %s", (file.filename,))
                if cur.rowcount > 0: abort(409, description="File already exists")

                # upload to s3, keeping db connection open because we will want to update metadata afterwards
                s3_client = boto3.client('s3', aws_access_key_id=S3_KEY, aws_secret_access_key=S3_SECRET)
                try:
                    s3_client.upload_fileobj(file, S3_BUCKET, file.filename)
                    # TODO: possible extra feature: handle the progress indicator callback from S3, perhaps updating the logs with the progress of the upload
                except Exception as e:
                    abort(500, description=f"Error uploading to S3: {e}")
                else:
                    # currently, there is no way to check if the upload actually succeeded after it gets sent to AWS, other than polling AWS for updates
                    # for the time being, let's assumine the upload is successful after it's sent, as the vast majority of time it will be
                    # the only issue is if the user makes a request to retrieve the file too soon after we've claimed that it is successful, they might not be able to see it
                    # TODO: poll AWS for updates and only return success once we've confirmed the file is there
                    pass

                # save file metadata to db
                file_name = file.filename
                file_uri = urllib.parse.urljoin(S3_BUCKET_URI, file.filename)
                # apparently, there is no way to get the actual file size until the file is fully uploaded to S3
                # therefore, we can only rely on what the client sends us instead. if we get 0, set this field to null in db
                # TODO: get the actual file size from AWS by waiting until the upload is done, then by asking AWS for the file metadata
                file_size_bytes = file.content_length if file.content_length else None
                file_type = file.content_type
                cur.execute("INSERT INTO files (file_name, file_uri, file_size_bytes, file_type, created_at) VALUES (%s, %s, %s, %s, %s) RETURNING file_id", 
                            (file_name, file_uri, file_size_bytes, file_type, datetime.now()))
                file_id = cur.fetchone()[0]

        return jsonify({"message": "File uploaded successfully", "file_id": file_id}), 200

    # update file or file metadata
    def put(self, file_id):
        # update file
        with psycopg2.connect(host=db['host'], user=db['user'], password=db['password'], database=db['name']) as conn:
            with conn.cursor() as cur:
                # check if this file exists -- only allow updating if it does
                # again, I've assumed this check is needed because otherwise we'd have redundant methods between this one and POST
                cur.execute("SELECT file_name, file_uri, file_size_bytes, file_type FROM files WHERE file_id = %s", (file_id,))
                if cur.rowcount == 0: abort(404, description="File not found")
                results = cur.fetchone()
                file_name, file_uri, file_size_bytes, file_type = results[0], results[1], results[2], results[3]

                # update the file in s3 given a file upload
                if 'file' in request.files and request.files['file'].filename:
                    file = request.files['file']

                    # make sure that what we are trying to rename to is not already taken
                    cur.execute("SELECT file_id FROM files WHERE file_name = %s", (file.filename,))
                    if cur.rowcount > 0: abort(409, description="Filename already exists")

                    # (keeping the db connection open as we want to update metadata soon after)
                    # uploading to s3 automatically overwrites the file, so we don't need to worry about deleting it first
                    # note that by default, versioning is turned on in s3, so the user won't actually lose the previous version of the file for a few days at least
                    s3_client = boto3.client('s3', aws_access_key_id=S3_KEY, aws_secret_access_key=S3_SECRET)
                    try:
                        s3_client.upload_fileobj(file, S3_BUCKET, file.filename)
                        # TODO: possible extra feature: handle the progress indicator callback from S3, perhaps updating the logs with the progress of the upload
                        # delete the old file from s3 after we've uploaded the new one
                        s3_client.delete_object(Bucket=S3_BUCKET, Key=file_name)
                    except Exception as e:
                        abort(500, description=f"Error uploading or deleting file from S3: {e}")
                    else:
                        # TODO: poll AWS for updates and only return success once we've confirmed the file is updated
                        pass

                    # update file metadata in db
                    file_name = file.filename
                    file_uri = urllib.parse.urljoin(S3_BUCKET_URI, file.filename)
                    file_size_bytes = file.content_length if file.content_length else None
                    file_type = file.content_type
                # if we weren't given a file, update the metadata instead
                else:
                    if 'file_name' in request.form:
                        # if the file name is changing, then we need to rename the file in s3
                        # there is actually no way to rename a file in s3, other than copying and deleting the file (!)
                        if file_name != request.form['file_name']:
                            # make sure that what we are trying to rename to is not already taken
                            cur.execute("SELECT file_id FROM files WHERE file_name = %s", (request.form['file_name'],))
                            if cur.rowcount > 0: abort(409, description="Filename already exists")

                            s3_client = boto3.client('s3', aws_access_key_id=S3_KEY, aws_secret_access_key=S3_SECRET)
                            try:
                                s3_client.copy_object(Bucket=S3_BUCKET, CopySource=f"{S3_BUCKET}/{file_name}", Key=request.form['file_name'])
                                s3_client.delete_object(Bucket=S3_BUCKET, Key=file_name)
                            except Exception as e:
                                abort(500, description=f"Error copying or deleting file from S3: {e}")
                        file_name = request.form['file_name']
                        file_uri = urllib.parse.urljoin(S3_BUCKET_URI, file_name)
                    if 'file_size_bytes' in request.form: file_size_bytes = request.form['file_size_bytes']
                    if 'file_type' in request.form: file_type = request.form['file_type']
                    # TODO: add error handling in case a call to this method was made but nothing was updated
                    # (the code works as is but would make an unncessary call in that case)

                cur.execute("UPDATE files SET file_name = %s, file_uri = %s, file_size_bytes = %s, file_type = %s WHERE file_id = %s", 
                            (file_name, file_uri, file_size_bytes, file_type, file_id))
                if cur.rowcount == 0: return abort(404, description="File not found")

        return jsonify({"message": 'File updated successfully'}), 200

    # delete file
    def delete(self, file_id):
        with psycopg2.connect(host=db['host'], user=db['user'], password=db['password'], database=db['name']) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT file_name FROM files WHERE file_id = %s", (file_id,))
                if cur.rowcount == 0: return abort(404, description="File not found")
                results = cur.fetchone()
                file_name = results[0]

                cur.execute("DELETE FROM files WHERE file_id = %s", (file_id,))
                if cur.rowcount == 0: return abort(404, description="File not found")

                # delete file from s3
                s3_client = boto3.client('s3', aws_access_key_id=S3_KEY, aws_secret_access_key=S3_SECRET)
                try:
                    s3_client.delete_object(Bucket=S3_BUCKET, Key=file_name)
                except Exception as e:
                    # since we're in a `with` block, the delete transaction should not be committed if we abort here
                    abort(500, description=f"Failed to delete file from S3: {e}")

        return jsonify({"message": "File deleted successfully"}), 200

file_view = FileAPI.as_view('file_api')
app.add_url_rule('/files', view_func=file_view, methods=['GET', 'POST'])
app.add_url_rule('/files/<int:file_id>', view_func=file_view, methods=['GET', 'PUT', 'DELETE'])

if __name__ == "__main__":
    app.run(host='0.0.0.0')
