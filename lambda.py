import boto3
import datetime

# AWS Clients
ec2 = boto3.client('ec2')
cloudwatch = boto3.client('cloudwatch')
sns = boto3.client('sns')
s3 = boto3.client('s3')
rds = boto3.client('rds')

# ---------------------------
# UPDATE THESE VALUES
# ---------------------------

# Replace with your SNS Topic ARN
SNS_TOPIC_ARN = "arn:aws:sns:us-east-1:512399768523:CostAlerts"

# Replace with your bucket name
S3_BUCKET_NAME = "cost-optimization-demo-yourname"

# ---------------------------
# Helper: Publish CloudWatch Metric
# ---------------------------
def publish_metric(metric_name, value):
    cloudwatch.put_metric_data(
        Namespace='CostOptimization',
        MetricData=[
            {
                'MetricName': metric_name,
                'Value': value,
                'Unit': 'Count'
            }
        ]
    )

# ---------------------------
# 1. EC2 Optimization
# ---------------------------
def check_idle_instances():
    instances = ec2.describe_instances(Filters=[{'Name': 'instance-state-name', 'Values': ['running']}])
    idle_instances = []

    for r in instances['Reservations']:
        for i in r['Instances']:
            iid = i['InstanceId']
            metrics = cloudwatch.get_metric_statistics(
                Namespace='AWS/EC2',
                MetricName='CPUUtilization',
                Dimensions=[{'Name': 'InstanceId', 'Value': iid}],
                StartTime=datetime.datetime.utcnow() - datetime.timedelta(minutes=60),
                EndTime=datetime.datetime.utcnow(),
                Period=300,
                Statistics=['Average']
            )
            if metrics['Datapoints']:
                avg_cpu = metrics['Datapoints'][0]['Average']
                if avg_cpu < 5:  # CPU < 5% means idle
                    idle_instances.append(iid)

    if idle_instances:
        ec2.stop_instances(InstanceIds=idle_instances)
        publish_metric("IdleEC2Stopped", len(idle_instances))

    return idle_instances

# ---------------------------
# 2. EBS Optimization
# ---------------------------
def check_unused_volumes():
    volumes = ec2.describe_volumes(Filters=[{'Name': 'status', 'Values': ['available']}])
    unused = [v['VolumeId'] for v in volumes['Volumes']]

    if unused:
        sns.publish(
            TopicArn=SNS_TOPIC_ARN,
            Subject="AWS Cost Alert: Unused EBS",
            Message=f"Unused EBS Volumes: {unused}"
        )
        publish_metric("UnusedEBSFound", len(unused))

    return unused

# ---------------------------
# 3. S3 Optimization
# ---------------------------
def optimize_s3(bucket_name):
    objects = s3.list_objects_v2(Bucket=bucket_name)
    moved = []

    if 'Contents' in objects:
        for obj in objects['Contents']:
            age = (datetime.datetime.now(datetime.timezone.utc) - obj['LastModified']).days
            if age > 30:  # Move objects older than 30 days
                s3.copy_object(
                    Bucket=bucket_name,
                    CopySource={'Bucket': bucket_name, 'Key': obj['Key']},
                    Key=obj['Key'],
                    StorageClass='STANDARD_IA'
                )
                moved.append(obj['Key'])

    if moved:
        publish_metric("S3ObjectsMoved", len(moved))

    return moved

# ---------------------------
# 4. RDS Optimization
# ---------------------------
def stop_idle_rds():
    dbs = rds.describe_db_instances()
    stopped = []

    for db in dbs['DBInstances']:
        if db['DBInstanceStatus'] == 'available' and "prod" not in db['DBInstanceIdentifier']:
            rds.stop_db_instance(DBInstanceIdentifier=db['DBInstanceIdentifier'])
            stopped.append(db['DBInstanceIdentifier'])

    if stopped:
        publish_metric("RDSStopped", len(stopped))

    return stopped

# ---------------------------
# Lambda Handler
# ---------------------------
publish_metric("IdleEC2Stopped", 1)
# Force publish 0 for all metrics, so CloudWatch always shows something
publish_metric("IdleEC2Stopped", 0)
publish_metric("UnusedEBSFound", 0)
publish_metric("S3ObjectsMoved", 0)
publish_metric("RDSStopped", 0)


def lambda_handler(event, context):
    stopped_ec2 = check_idle_instances()
    unused_volumes = check_unused_volumes()
    moved_s3 = optimize_s3(S3_BUCKET_NAME)
    stopped_rds = stop_idle_rds()

    return {
        "statusCode": 200,
        "body": {
            "EC2 stopped": stopped_ec2,
            "Unused EBS": unused_volumes,
            "S3 moved": moved_s3,
            "RDS stopped": stopped_rds
        }
    }

