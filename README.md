## OBJECT POSITION SERVER

This is a python server that provides positions of objects that transmit in the L1 band. These are the objects that are visible to the TART telescope. This server can be used to get an expected sky, by requesting the

and, as an example, the following url (https://tart.elec.ac.nz/catalog/catalog?lat=-45.85&lon=170.54&date=now) will show all the objects above the Dunedin, New Zealand TART.

### API Reference

The API reference generated from the code here is online (https://tart.elec.ac.nz/catalog/doc/index.html)

Author: Tim Molteno. tim@elec.ac.nz

## Prerequisites

A computer with [docker](https://docker.io) installed, and having external web access to software repositories. We have tested these instructions only on Linux-based computers.

## Docker For Object Position Server

The easiest way to build this is to use docker. To build the container type

    docker compose build

To run it (the -d puts it in the background)

    docker compose up -d

This creates an instance called 'ops'. You can check the logs using 

    docker attach ops

To exit type Ctrl-p Ctrl-q

    
To kill the instance

    docker compose down

## More accurate Power estimates

Steigenberger, Peter, Steffen Thoelert, and Oliver Montenbruck. "GNSS satellite transmit power and its impact on orbit determination." Journal of Geodesy 92.6 (2018): 609-624.


## Testing

Point your browser to the documentation at http://localhost:8877. You can try the following URL 

    wget -qO- "http://localhost:8876/catalog?lat=-45.85&lon=170.54"

Testing the bulk_az_el endpoint is a bit more complicated because it requires a POST request with JSON data.
    
    import requests
    json_data =  {"lat": 45.5, "lon": 170.5, "alt": 0, "dates": ["2023-12-07T09:25:55.924113", "2023-12-07T09:25:55.924113"]}
    r = requests.post('http://localhost:8876/bulk_az_el', json=json_data)
   
    curl -X POST -H "Content-type: application/json" -d "{\"lat\": 45.5, \"lon\": 170.5, \"alt\": 0, \
               \"dates\": [\"2023-12-07T09:25:55.924113\", \"2023-12-07T09:25:55.924113\"]}" "localhost:8876/bulk_az_el"
