# concordia


## Prerequisites

This application can run on a single Docker host using docker-compose. (recommended for development environments). For production, see the cloudformation directory for AWS Elastic Container stack templates.

## Passwords

This project stores passwords in a file named `.env`. You must create this file in the concordia root directory. An example of a `.env` file is in the top level source directory named `example_env_file`.

This file contains:

    GRAFANA_ADMIN_PW=<grafana_admin_password_here>
    CONCORDIA_ADMIN_PW=<concordia_admin_password_here>
    POSTGRESQL_PW=<postgresql_concordia_user_password_here>

    SENTRY_PW=<sentry_password_here>
    SENTRY_SECRET_KEY=<sentry_secret_key_here>

    EMAIL_HOST=<your_smtp_email_host_here>
    EMAIL_HOST_USER=<your_smtp_email_host_user_here>
    EMAIL_HOST_PASSWORD=<your_smtp_email_host_password_here>
    DEFAULT_FROM_EMAIL=<your_email_from_address_here>

    AWS_ACCESS_KEY_ID=<aws_access_key_id_here>
    AWS_SECRET_ACCESS_KEY=<aws_secret_access_key_here>

Setting the passwords in this file is the only location where user
passwords are defined. All access to these passwords is through the `.env`
file.


### Sentry Secret Key

The sentry secret key must be generated and placed into the `.env` file.
To generate the key, run the command:

    $ docker-compose -f docker-compose-sentry.yml run --rm sentry sentry config generate-secret-key

The sentry key is a long value output from this command. Substitute the
value for `<sentry_secret_key_here>` with the value generated by this
command.

Note: If any open or close parenthesis (or other special characters) are generated in the key, they
must be preceded by a backslash, `\`, when the value is added to the
`.env` file.

## Running Concordia

    $ git clone https://github.com/LibraryOfCongress/concordia.git
    $ cd concordia
    $ docker-compose up

Browse to [localhost](http://localhost)

### Development Environment

You may wish to run the Django development server on your localhost
instead of within a Docker container. It is easy to set up a Python
virtual environment to work in.

#### Serve

Instead of doing `docker-compose up` as above, instead do the following:

    $ docker-compose up -d db
    $ docker-compose up -d rabbit
    $ docker-compose up -d importer

This will keep the database in its container for convenience.

Next, set up a Python virtual environment, install pipenv
\<<https://docs.pipenv.org/>\>, and other Python prerequisites:

    $ python3 -m venv .venv
    $ source .venv/bin/activate
    $ pip3 install pipenv
    $ pipenv install --dev

Finally, configure the Django settings, run migrations, and launch the
development server:

    $ export DJANGO_SETTINGS_MODULE="concordia.settings_dev"
    $ ./manage.py migrate
    $ ./manage.py runserver

#### Code Quality

Install black \<<https://pypi.org/project/black/>\> and integrate it
with your editor of choice. Run flake8
\<<http://flake8.pycqa.org/en/latest/>\> to ensure you don't increase
the warning count or introduce errors with your commits. This project
uses EditorConfig \<<https://editorconfig.org>\> for code consistency.

Django projects should extend the standard Django settings model for
project configuration. Django projects should also make use of the
Django test framework for unit tests.

setup.cfg contains configuration for pycodestyle, isort
\<<https://pypi.org/project/isort/>\> and flake8.

The virtual env directory should be named .venv and it's preferred to
use Pipenv to manage the virtual environment.

Configure your editor to run black and isort on each file at save time.

If you can't modify your editor, here is how to run the code quality
tools manually:

    $ black .
    $ isort --recursive .

Black should be run prior to isort. It's recommended to commit your code
before running black, after running black, and after running isort so
the changes from each step are visible.

#### Data Model Graph


To generate a model graph, do:

    $ docker-compose up -d app
    $ docker-compose exec app bash
    # cd concordia/static/img
    # python3 ./manage.py graph_models concordia > tx.dot
    # dot -Tsvg tx.dot -o tx.svg

#### Python Dependencies


Python dependencies are managed using pipenv
\<<https://docs.pipenv.org/>\>.

If you want to add a new Python package requirement to the application
environment, it must be added to the Pipfile and the Pipfile.lock file.
This can be done with the command:

	$ pipenv install <package>

If you manually add package names to Pipfile, then you need to update
the Pipfile.lock file:

	$ pipenv lock

Both the Pipfile and the Pipfile.lock file must be committed to the
source code repository.


#### Deployment Environment

If you are developing with concordia and you want to use any of the 
deployment environment tools in your development environment, you can use the following.

##### Sentry for log aggregation

    $ make up

##### Prometheus with Grafana for application health monitoring

    $ docker-compose -f docker-compose-prometheus.yml

##### ElasticSearch and Kibana for data visualization

    $ docker-compose -f docker-compose-elk.yml

#### Everything

    $ docker-compose -f docker-compose-elk.yml -f docker-compose-prometheus.yml -f docker-compose-sentry.yml -f docker-compose.yml up