from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import ConnectionType
from azure.identity import DefaultAzureCredential
from azure.core.credentials import AzureKeyCredential
from azure.search.documents import SearchClient
from azure.search.documents.indexes import SearchIndexClient
from config import get_logger
from creole import creole2html
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_text_splitters import HTMLHeaderTextSplitter

import os
import fitz  # PyMuPDF
import hashlib

# initialize logging object
logger = get_logger(__name__)

# create a project client using environment variables loaded from the .env file
project = AIProjectClient.from_connection_string(
    conn_str=os.environ["AIPROJECT_CONNECTION_STRING"], credential=DefaultAzureCredential()
)

# create a vector embeddings client that will be used to generate vector embeddings
embeddings = project.inference.get_embeddings_client()

# use the project client to get the default search connection
search_connection = project.connections.get_default(
    connection_type=ConnectionType.AZURE_AI_SEARCH, include_credentials=True
)

# Create a search index client using the search connection
# This client will be used to create and delete search indexes
index_client = SearchIndexClient(
    endpoint=search_connection.endpoint_url, credential=AzureKeyCredential(key=search_connection.key)
)

import pandas as pd
from azure.search.documents.indexes.models import (
    SemanticSearch,
    SearchField,
    SimpleField,
    SearchableField,
    SearchFieldDataType,
    SemanticConfiguration,
    SemanticPrioritizedFields,
    SemanticField,
    VectorSearch,
    HnswAlgorithmConfiguration,
    VectorSearchAlgorithmKind,
    HnswParameters,
    VectorSearchAlgorithmMetric,
    ExhaustiveKnnAlgorithmConfiguration,
    ExhaustiveKnnParameters,
    VectorSearchProfile,
    SearchIndex,
)


def create_index_definition(index_name: str, model: str) -> SearchIndex:
    dimensions = 1536  # text-embedding-ada-002
    if model == "text-embedding-3-large":
        dimensions = 3072

    # The fields we want to index. The "embedding" field is a vector field that will
    # be used for vector search.
    fields = [
        SimpleField(name="id", type=SearchFieldDataType.String, key=True),
        SearchableField(name="content", type=SearchFieldDataType.String),
        SimpleField(name="filepath", type=SearchFieldDataType.String),
        SearchableField(name="title", type=SearchFieldDataType.String),
        SimpleField(name="url", type=SearchFieldDataType.String),
        SearchField(
            name="contentVector",
            type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
            searchable=True,
            # Size of the vector created by the text-embedding-ada-002 model.
            vector_search_dimensions=dimensions,
            vector_search_profile_name="myHnswProfile",
        ),
    ]

    # The "content" field should be prioritized for semantic ranking.
    semantic_config = SemanticConfiguration(
        name="default",
        prioritized_fields=SemanticPrioritizedFields(
            title_field=SemanticField(field_name="title"),
            keywords_fields=[],
            content_fields=[SemanticField(field_name="content")],
        ),
    )

    # For vector search, we want to use the HNSW (Hierarchical Navigable Small World)
    # algorithm (a type of approximate nearest neighbor search algorithm) with cosine
    # distance.
    vector_search = VectorSearch(
        algorithms=[
            HnswAlgorithmConfiguration(
                name="myHnsw",
                kind=VectorSearchAlgorithmKind.HNSW,
                parameters=HnswParameters(
                    m=4,
                    ef_construction=1000,
                    ef_search=1000,
                    metric=VectorSearchAlgorithmMetric.COSINE,
                ),
            ),
            ExhaustiveKnnAlgorithmConfiguration(
                name="myExhaustiveKnn",
                kind=VectorSearchAlgorithmKind.EXHAUSTIVE_KNN,
                parameters=ExhaustiveKnnParameters(metric=VectorSearchAlgorithmMetric.COSINE),
            ),
        ],
        profiles=[
            VectorSearchProfile(
                name="myHnswProfile",
                algorithm_configuration_name="myHnsw",
            ),
            VectorSearchProfile(
                name="myExhaustiveKnnProfile",
                algorithm_configuration_name="myExhaustiveKnn",
            ),
        ],
    )

    # Create the semantic settings with the configuration
    semantic_search = SemanticSearch(configurations=[semantic_config])

    # Create the search index definition
    return SearchIndex(
        name=index_name,
        fields=fields,
        semantic_search=semantic_search,
        vector_search=vector_search,
    )

# define a function for indexing a csv file, that adds each row as a document
# and generates vector embeddings for the specified content_column
def create_docs_from_csv(path: str, content_column: str, model: str) -> list[dict[str, any]]:
    products = pd.read_csv(path)
    items = []
    for product in products.to_dict("records"):
        content = product[content_column]
        id = str(product["id"])
        title = product["name"]
        url = f"/products/{title.lower().replace(' ', '-')}"
        emb = embeddings.embed(input=content, model=model)
        rec = {
            "id": id,
            "content": content,
            "filepath": f"{title.lower().replace(' ', '-')}",
            "title": title,
            "url": url,
            "contentVector": emb.data[0].embedding,
        }
        items.append(rec)

    return items

 
def extract_text_from_pdfs(pdf_dir, model: str):
    text_data = []
    files = os.listdir(pdf_dir)    
    for file in files:
        # Full path to the file
        full_path = os.path.join(pdf_dir, file)        
        text_data.extend (extract_text_from_pdf(full_path, model))
    return text_data
        
def extract_text_from_pdf(pdf_path, model: str):
    text_data = []
    file_name = os.path.basename(pdf_path)
    logger.info(f"Processing file {file_name}")
    name = os.path.splitext(file_name)[0]
 
    pdf_document = fitz.Document(pdf_path)

    for page_num in range(pdf_document.page_count):
        page = pdf_document.load_page(page_num)
        text = page.get_text()
        id = get_file_hash (pdf_path)
        emb = embeddings.embed(input=text, model=model)        
        text_data.append({
            "id": id, 
            "content": text, 
            "filepath": file_name, 
            "title": name, 
            "url": pdf_path, 
            "contentVector": emb.data[0].embedding,
        })
 
    return text_data

def extract_text_from_web_page(
    initial_url,
    model
) :
    """Load data from the urls.

    Args:
        urls (List[str]): List of URLs to scrape.
        custom_hostname (Optional[str]): Force a certain hostname in the case
            a website is displayed under custom URLs (e.g. Substack blogs)
        include_url_in_text (Optional[bool]): Include the reference url in the text of the document

    Returns:
        List[Document]: List of documents.

    """
    from urllib.parse import urlparse

    import requests
    from bs4 import BeautifulSoup

    documents = []
    urls = [initial_url]    
    cookies = {'JSESSIONID': 'ED4CEED48F7F2272F4C8ABC530D5D011'}

    while len(urls) != 0:
        url = urls.pop()
        print (f'Processing ...{url[-20:]}')
        page = requests.get(url, cookies=cookies)
        status_code = page.status_code
        print (f'http status {status_code}')
        if status_code == 200:
            soup = BeautifulSoup(page.content, "html.parser")
            title = soup.find('title')
            text = soup.getText()

            id = get_hash (text)
            emb = embeddings.embed(input=text, model=model)        
            documents.append({
                "id": id, 
                "content": text, 
                "filepath": url, 
                "title": title, 
                "url": url, 
                "contentVector": emb.data[0].embedding,
            })

            link_elements = soup.select("a[href]")        

            for link_element in link_elements:
                link_url = link_element['href']
                if initial_url in link_url:
                    urls.append (link_url)
        else:
            print ('skipping')

    return documents       

def extract_text_from_db(
    host, user, password, database, model
) :
    import mysql.connector    
    
    mydb = mysql.connector.connect(
        host=host,
        user=user,
        password=password,
        database=database
    )

    mycursor = mydb.cursor()

    mycursor.execute("""
        SELECT * 
        FROM ( 
            SELECT  b.description, c.title, a.content, a.format, ROW_NUMBER() OVER (PARTITION BY a.resourcePrimKey ORDER BY a.version DESC) AS row_num 
            FROM WikiPage a 
            join WikiNode b on b.nodeId = a.nodeId 
            join WikiPageResource c on c.resourcePrimKey = a.resourcePrimKey 
        ) ranked 
        WHERE row_num = 1
        AND LENGTH (content) > 0
        and description <> ''
    """)

    rows = mycursor.fetchall()
    row_idx = 0
    row_count = len (rows)

    for row in rows:
        documents = []
        row_idx += 1
        description = row[0]
        title = row[1]
        text = row[2]
        format = row[3]
        url = f"https://home.intesys.it/wiki/-/wiki/{description.replace(' ', '+')}/{title.replace(' ', '+')}"
        print (f'Processing page {row_idx}/{row_count} - {title}')

        chunks = split_content(text, format)
        for i, chunk in enumerate(chunks):
            id = get_hash((title, i, chunk.page_content))
            emb = embeddings.embed(input=chunk.page_content, model=model)        
            documents.append({
                "id": id, 
                "content": chunk.page_content, 
                "filepath": url, 
                "title": title, 
                "url": url, 
                "contentVector": emb.data[0].embedding,
            })

        yield documents
    

def split_content(html_string, format):
    if format == 'creole':
        return split_creole (html_string)
    raise Exception (format)    

def split_creole (creole):
    html = creole2html (creole)    

    headers_to_split_on = [
        ("h1", "Header 1"),
        ("h2", "Header 2"),
        ("h3", "Header 3"),
        ("h4", "Header 4"),
    ]

    html_splitter = HTMLHeaderTextSplitter(headers_to_split_on=headers_to_split_on)

    # for local file use html_splitter.split_text_from_file(<path_to_file>)
    html_header_splits = html_splitter.split_text (html)

    chunk_size = 500
    chunk_overlap = 30
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size, chunk_overlap=chunk_overlap
    )

    # Split
    return text_splitter.split_documents(html_header_splits)

def create_index_from_web_page(index_name, initial_url):
    # If a search index already exists, delete it:
    try:
        index_definition = index_client.get_index(index_name)
        index_client.delete_index(index_name)
        logger.info(f"🗑  Found existing index named '{index_name}', and deleted it")
    except Exception:
        pass

    # create an empty search index
    index_definition = create_index_definition(index_name, model=os.environ["EMBEDDINGS_MODEL"])
    index_client.create_index(index_definition)

    # create documents from the products.csv file, generating vector embeddings for the "description" column        
    docs = extract_text_from_web_page(initial_url=initial_url, model=os.environ["EMBEDDINGS_MODEL"])

    # Add the documents to the index using the Azure AI Search client
    search_client = SearchClient(
        endpoint=search_connection.endpoint_url,
        index_name=index_name,
        credential=AzureKeyCredential(key=search_connection.key),
    )

    search_client.upload_documents(docs)
    logger.info(f"{len(docs)} documents uploaded to '{index_name}'")

def create_index_from_db(index_name, host, user, password, database, delete_existing):
    # If a search index already exists, delete it:
    try:
        index_definition = index_client.get_index(index_name)
        if delete_existing:
            index_client.delete_index(index_name)
            logger.info(f"🗑  Found existing index named '{index_name}', and deleted it")
    except Exception:
        pass

    # create an empty search index
    index_definition = create_index_definition(index_name, model=os.environ["EMBEDDINGS_MODEL"])
    index_client.create_index(index_definition)

    # Add the documents to the index using the Azure AI Search client
    search_client = SearchClient(
        endpoint=search_connection.endpoint_url,
        index_name=index_name,
        credential=AzureKeyCredential(key=search_connection.key),
    )

    # create documents from the products.csv file, generating vector embeddings for the "description" column        
    for docs in extract_text_from_db(host=host, user=user, password=password, database=database, model=os.environ["EMBEDDINGS_MODEL"]):
        try:
            if (len (docs) > 0):                
                search_client.upload_documents(docs)
                logger.info(f"{len(docs)} documents uploaded to '{index_name}'")
            else:
                logger.warning("Nothing to upload")    
        except Exception as e:
            logger.info(f'Upload failed: {e.args} ({type(e)})')


def create_index_from_pdfs(index_name, pdf_dir):
    # If a search index already exists, delete it:
    try:
        index_definition = index_client.get_index(index_name)
        index_client.delete_index(index_name)
        logger.info(f"🗑  Found existing index named '{index_name}', and deleted it")
    except Exception:
        pass

    # create an empty search index
    index_definition = create_index_definition(index_name, model=os.environ["EMBEDDINGS_MODEL"])
    index_client.create_index(index_definition)

    # create documents from the products.csv file, generating vector embeddings for the "description" column
    docs = extract_text_from_pdfs(pdf_dir=pdf_dir, model=os.environ["EMBEDDINGS_MODEL"])

    # Add the documents to the index using the Azure AI Search client
    search_client = SearchClient(
        endpoint=search_connection.endpoint_url,
        index_name=index_name,
        credential=AzureKeyCredential(key=search_connection.key),
    )

    search_client.upload_documents(docs)
    logger.info(f"Uploaded {len(docs)} documents to '{index_name}' index")

def get_hash(content, algorithm='sha256'):
    """
    Generate hash of file content
    
    Args:
        content (str): vontent of file
        algorithm (str, optional): Hash algorithm to use. Defaults to 'sha256'.
    
    Returns:
        str: Hexadecimal hash of file content
    """
    hash_algorithms = {
        'md5': hashlib.md5,
        'sha1': hashlib.sha1,
        'sha256': hashlib.sha256,
        'sha512': hashlib.sha512
    }
    
    # Select hash algorithm
    hash_func = hash_algorithms.get(algorithm.lower())
    if not hash_func:
        raise ValueError(f"Unsupported hash algorithm: {algorithm}")
    
    # Create hash object
    hash_obj = hash_func()
    
    for element in content:
        # Read and hash file in chunks to handle large files
        hash_obj.update(str (element).encode(encoding = 'UTF-8', errors = 'strict'))
    
    return hash_obj.hexdigest()

def get_file_hash(file_path, algorithm='sha256'):
    """
    Generate hash of file content
    
    Args:
        file_path (str): Path to the file
        algorithm (str, optional): Hash algorithm to use. Defaults to 'sha256'.
    
    Returns:
        str: Hexadecimal hash of file content
    """
    hash_algorithms = {
        'md5': hashlib.md5,
        'sha1': hashlib.sha1,
        'sha256': hashlib.sha256,
        'sha512': hashlib.sha512
    }
    
    try:
        # Select hash algorithm
        hash_func = hash_algorithms.get(algorithm.lower())
        if not hash_func:
            raise ValueError(f"Unsupported hash algorithm: {algorithm}")
        
        # Create hash object
        hash_obj = hash_func()
        
        # Read and hash file in chunks to handle large files
        with open(file_path, 'rb') as file:
            for chunk in iter(lambda: file.read(4096), b''):
                hash_obj.update(chunk)
        
        return hash_obj.hexdigest()
    
    except FileNotFoundError:
        print(f"File not found: {file_path}")
    except PermissionError:
        print(f"Permission denied: {file_path}")
    except Exception as e:
        print(f"Error hashing file: {e}")

if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--index-name",
        type=str,
        help="index name to use when creating the AI Search index",
        default=os.environ["AISEARCH_INDEX_NAME"],
    )
    parser.add_argument(
        "--host", 
        type=str, 
        help="database host", 
        default="192.168.1.110"
    )
    parser.add_argument(
        "--user", 
        type=str, 
        help="database user", 
        default="ext.read.user"
    )
    parser.add_argument(
        "--password", 
        type=str, 
        help="database password",
        required=True
    )
    parser.add_argument(
        "--database", 
        type=str, 
        help="database name", 
        default="lportal711_prod_utf8mb4"
    )
    parser.add_argument(
        "--delete-exising", 
        type=bool, 
        help="delete existing index", 
        default=False
    )

    args = parser.parse_args()
    create_index_from_db(args.index_name, user=args.user, password=args.password, host=args.host, database=args.database, delete_existing=args.delete_exising)
