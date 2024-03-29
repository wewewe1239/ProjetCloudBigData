import time
from private_config import ACCESS_KEY, SECRET_KEY, username, REGION_NAME
import argparse
import boto3
from botocore.exceptions import ClientError

from key_pair import create_key_pair
from security_group import create_security_group
from create_instances import create_instances
from cluster_k8s_ssh import lancer_k8s_ssh, lancer_spark_on_k8s_ssh
from utils import is_checking, is_pending
import kubeopex

DEFAULT_NUMBER_MASTERS = 1
DEFAULT_NUMBER_WORKERS = 2

USER = None
NUMBER_MASTERS = None
NUMBER_WORKERS = None
NUMBER_NODES = None
KEY_NAME = None
SECURITY_GROUP = "lessanchos"
SECURITY_GROUP_DESC = "Pour notre cluster K8s"

CLUSTER = {"Masters": [], "Slaves": []}


def parse_arguments():

    global USER, KEY_NAME, NUMBER_MASTERS, NUMBER_WORKERS, NUMBER_NODES

    parser = argparse.ArgumentParser(
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        description="""Create an AWS cluster and deploy kubernetes in it""",
    )

    parser.add_argument(
        "-u",
        "--user",
        dest="user",
        type=str,
        default=username,
        help="Specify a username",
    )

    parser.add_argument(
        "-m",
        "--masters",
        dest="nb_masters",
        type=int,
        default=DEFAULT_NUMBER_MASTERS,
        help="Specify the number of masters",
    )

    parser.add_argument(
        "-w",
        "--workers",
        dest="nb_workers",
        type=int,
        default=DEFAULT_NUMBER_WORKERS,
        help="Specify the number of workers",
    )

    args = parser.parse_args()

    USER = args.user or username
    KEY_NAME = USER + '_key'
    NUMBER_MASTERS = args.nb_masters or DEFAULT_NUMBER_MASTERS
    NUMBER_WORKERS = args.nb_workers or DEFAULT_NUMBER_WORKERS
    NUMBER_NODES = NUMBER_MASTERS + NUMBER_WORKERS

    print("Deploying cluster with the following parameters : ")
    print(vars(args))


if __name__ == "__main__":

    parse_arguments()

    ec2 = boto3.client(
        "ec2", aws_access_key_id=ACCESS_KEY, aws_secret_access_key=SECRET_KEY, region_name=REGION_NAME,
    )

    ec2_resource = boto3.resource(
        "ec2", aws_access_key_id=ACCESS_KEY, aws_secret_access_key=SECRET_KEY, region_name=REGION_NAME
    )

    # Key pairs
    print("\nGenerating keypairs")
    try:
        create_key_pair(ec2, name=USER+"_key")
    except Exception as e:
        print(e)

    # Security groups
    print("\nGenerating security group : " + SECURITY_GROUP)
    security_group = create_security_group(
        ec2, ec2_resource, name=SECURITY_GROUP, description=SECURITY_GROUP_DESC)

    # Instances
    print("\nLaunching instances ...")
    [master_instances, slave_instances] = create_instances(
        ec2_resource, security_group, NUMBER_WORKERS, NUMBER_MASTERS, KEY_NAME)

    # Il faut le temps que les instances soient créées et dans l'état "running"
    ids = [instance.id for instance in master_instances] + [instance.id for instance in slave_instances]
    id_filter = [
        {
            'Name': 'instance-id',
            'Values': ids
        },
    ]
    print("    Instances are : "+str(ids))
    print("Waiting for running... (approx. 20sec)")
    while (is_pending(id_filter, ec2)):
        time.sleep(3)
    print("Instances running !")
    print("Waiting for checks... (approx. 150sec)")
    while (is_checking(ids, ec2)):
        time.sleep(10)
    print("Instances checked !")
    print("Waiting for boot... (10sec)")
    time.sleep(10)

    # Remplissage du dictionnaire permettant de centraliser les infos sur les slaves et masters
    for instance in master_instances:
        CLUSTER["Masters"].append(
            {
                "Id_Instance": instance.id,
                "Ip_Address": ec2_resource.Instance(instance.id).public_ip_address,
                "Dns_Name": ec2_resource.Instance(instance.id).public_dns_name,
                "Private_Ip_Address": ec2_resource.Instance(instance.id).private_ip_address
            }
        )

    num_slave = 1
    for instance in slave_instances:
        CLUSTER["Slaves"].append(
            {
                "Id_Slave": "slave" + str(num_slave),
                "Id_Instance": instance.id,
                "Ip_Address": ec2_resource.Instance(instance.id).public_ip_address,
                "Dns_Name": ec2_resource.Instance(instance.id).public_dns_name
            }
        )
        num_slave += 1

    # Lancement du cluster K8s
    print("\nLaunching the k8s cluster... ")
    print("Cluster is : ")
    print("Masters")
    for master in CLUSTER['Masters']:
        print("    " + str(master['Id_Instance']) + " at " +
              str(master['Ip_Address']) + " under " + str(master['Dns_Name']))
    print("Slaves")
    for slaves in CLUSTER['Slaves']:
        print("    " + str(slaves['Id_Instance']) + " at " +
              str(slaves['Ip_Address']) + " under " + str(slaves['Dns_Name']))

    print("Waiting another 40sec... ")
    time.sleep(40)
    print("\n\n Launching the kubernetes cluster...")
    lancer_k8s_ssh(CLUSTER)

    print("\n\n Launching kube-opex-analytics on master...")
    kubeopex.launch(CLUSTER['Masters'][0], KEY_NAME)

    print("\n\n Launching spark on the kubernetes cluster...")
    lancer_spark_on_k8s_ssh(CLUSTER)
    
    print("Deployed successfully !")