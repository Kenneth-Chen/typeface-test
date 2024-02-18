# To run:

```
docker run -p 8000:8000 --env-file ./.env typeface-test
```

# Notes on tech choices

I went with Python/Flask using PostgreSQL hosted on AWS RDS for the db, and with AWS S3 for file storage.

I went with Python/Flask just because I was familiar with it and because it's popular and has wide support. For the database, I felt that a relational db made the most sense, as I imagine a larger and more complex version of this system needing many complex queries to be run on it. I chose PostgreSQL because it handles a larger volume of writes compared to MySQL, and because it is very well supported, with excellent documentation. I also wanted to run the db server on the cloud so that it can scale separately from the compute instances and from file storage. I like to go with AWS because it has the biggest market share out of all the cloud providers at around 40%, which means a great base of support, a great developer community, and because it is the most likely to survive in the long-run. Between AWS RDS and AWS Aurora, AWS Aurora is in theory perhaps a better choice for scalability, although being a fully managed service has it drawbacks -- one becomes more dependent on AWS for troubleshooting, and there have been reports of poor communication from their team in the past. There's not too much of a difference at smaller scale though, and given that we're planning on using the db purely for metadata, it's not likely to grow to enormous scale (even at 1 billion users and 10KB of metadata per user, we would only reach 10TB), so I went with RDS for simplicity.

S3 for file storage is fantastic, because it's got built-in scalability, replication, multi-part uploading, automatic archive of files rarely accessed, etc. If I had more time, I would also set up AWS CloudFront, as the files we're hosting are static and benefit greatly from all the features of a CDN and its location-based caching. Note that RDS and S3 are currently setup on my personal account, so no additional setup should be necessary (for convenience, I included the credentials in the image; of course in a real-world scenario I would never do this)

# DB Schema

For the database schema, I created the following table:

```
CREATE TABLE files (
    file_id SERIAL NOT NULL PRIMARY KEY,
    file_name VARCHAR(512) NOT NULL,
    file_uri VARCHAR(4096) NOT NULL,
    file_size_bytes BIGINT,
    file_type VARCHAR(256),
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE (file_name, file_uri)
);
```

In addition to the requested fields, I also wanted to store the file URI for convenience, and it is computed based on the S3 bucket name, so the user doesn't need to worry about specifying it. Of course, the file URI should be unique. I also had to make the file name unique in order to prevent namespace collisions when the user uploads another file with the same name. In a real system, file names would probably not be unique -- only the combination of a user_id and file_name pairing would be unique, as certainly different users should be able to have different files with the same name. In a real system, we'd probably also want to store the relative path to a file, in order to have some kind of directory structure, or else a single user's Dropbox would become unwieldy quite fast.

We want there to be indexes on both file_id and file_name since the code queries on those fields, but those indexes would already be created by default from the primary key specification and the unique constraint specification.

File size and file type are nullable columns. When uploading to S3, there isn't really an easy way to determine the file size and file type except for the metadata provided by the client (which may be missing or incorrect) until the file finishes getting uploaded, so I wanted to leave the option open to keep these fields null, especially in the case of a large multi-part upload. 

# Testing

To test the API, I used curl. Here are the commands I used:

list all files
```
curl -X GET http://localhost:8000/files
```

get a single file
```
curl -X GET http://localhost:8000/files/1
```

upload a new file
```
curl -X POST -F "file=@test.dat" http://localhost:8000/files
```

upload a new file but give it a different name
```
curl -X POST -F "file=@test.dat;filename=test.txt" http://localhost:8000/files
```

update a file by providing the updated file binary
```
curl -X PUT -F "file=@test.dat" http://localhost:8000/files/1
```

update a file by providing the updated file binary, and change its name
```
curl -X PUT -F "file=@test.dat;filename=test.txt" http://localhost:8000/files/1
```

update the metadata of a file
```
curl -X PUT http://localhost:8000/files/1 --data 'file_name=banana.txt&file_size_bytes=1024&file_type=text/plain'
```

delete a file
```
curl -X DELETE http://localhost:8000/files/1
```

Note that retrieving a file actually returns a redirect to the file URI on S3. I thought it didn't make a lot of sense to try and download the file from S3 and then try to return the file binary in the response body, as this would negate a lot of the advantages of S3 and CloudFront. On a separate note, retrieving the file metadata is relatively easy and requires little modification of the existing code, should that endpoint be required at some point.


# Further improvements

* Currently, there is no easy way to check if a file has finished uploading to S3 after the file itself was already sent to S3, other than by polling S3. This shouldn't be an issue in the vast majority of cases, but it is theoretically possible to get a success response after uploading, yet not be able to see the file on S3 just yet.

* Devise a more reliable and accurate way to compute the file size and type after uploading to S3, other than relying on the metadata provided by the client.

* Potentially detect if the file size is too large and throw an error if it is.

* Allow users to resume uploads of large files if interrupted.

* Encryption of files in transit and in storage. I imagine people would want a place to store their sensitive documents on a Dropbox-like service, so this feature should be pretty important.

* Directory structure and file management for users.

* Allowing multiple users to use the system; incorporate login (possibly oauth and 2FA).

* S3 already provides document versioning by default. It wouldn't be too difficult to implement a simple versioning system that could allow users to retrieve different versions. Documents that were accidentally deleted could also be recovered by leveraging this feature. Finally, for some users, an audit trail feature that takes advantage of this version data might prove invaluable.

* AWS provides a callback function when uploading a file, which provides an intermittent status update as to the upload progress. Being able to store and return this information might prove useful for especially large files, and at the very least, it could get printed in the logs.
