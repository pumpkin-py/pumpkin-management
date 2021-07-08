# Template

This repository is template for [pumpkin.py] module repositories.

The key words "MUST", "MUST NOT", "REQUIRED", "SHALL", "SHALL NOT", "SHOULD", "SHOULD NOT", "RECOMMENDED",  "MAY", and "OPTIONAL" in this document are to be interpreted as described in [RFC 2119](https://tools.ietf.org/html/rfc2119).

```
template
+- __init__.py
+- README.md
+- CHANGELOG.md
+- requirements.txt
'- talk/
   +- __init__.py
   +- module.py
   +- database.py
   +- lang/
      +- en.ini
      '- cs.ini
```

## \_\_init\_\_.py

This file MUST be present in your repository.

It MUST include those three variables:

- `__name__`: Name of the repository. It has to be unique and can only contain lowercase ASCII letters and a dash (`[a-z-]+`) and MUST NOT be `core` or `base`. If a repository of this name already exists, the bot will refuse it. Check the [pumpkin.py] for the list of known third-party repositories. Moderators can run the **repository list** command to show what repositores are available.
- `__version__`: Version of your repository. This MUST follow the [semver](https://semver.org/) rules.
- `__all__`: A tuple of all modules included in the repository. Every string in the tuple has to have its module directory.

## README.md

The readme SHOULD be present in your repository.

In this file, you should describe the reasoning for creation of the repository and its modules.

The pumpkin.py ignores this file.

## CHANGELOG.rst

The changelog SHOULD be present in your repository.

For each version you create, you MUST add a second-level heading with the version number and a text content, which SHOULD be a list, but MAY be just a plain text. See the example [CHANGELOG.rst](CHANGELOG.rst) file.

Future versions of pumpkin.py may parse this file in order to display changelog information.

## requirements.txt

The requirements file MAY be present in the repository.

If the file exists, the bot will use standard python tools to install packages from this file. You MUST NOT add packages you are not using in your modules. Use `requirements-dev.txt` for development packages.

The pumpkin.py installs packages specified in this file.

## (module directory)

For every module specified in the `__all__` variable you MUST create a directory with a `module.py` file.

This file is used by the bot to load the modules (cogs), so it has to include the discord.py's `setup()` function. See the `lang/module.py` for code examples.

You MUST include any files required for your modules to work here, or have them linked as URLs.

### module database

If your cog uses database in any way, the sqlalchemy tables MUST be placed in `database.py` file.

Each table class should be named as `RepoModuleFunctionality` with table name of `repo_module_functionality`. Each entry SHOULD have primary `id` column and MUST be guild-wide, meaning that information on server A shouldn't be accesible on server B, by using `guild_id` column. Channel column should be named `channel_id`, message `message_id` and user's/member's `user_id` if applicable; if there are multiple users or channels, name them in a meaningful, describing way. All database tables MUST have `__repr__` and MAY have `__str__` functions. Database operations (`get`, `add`, `remove`) SHOULD be implemented as `@staticmethod`s, unless there is a good reason not to do it.

```py
from sqlalchemy import Column, Integer, BigInteger, String

from database import database, session


class FooBarBaz(database.base):
	"""Foo's repo Baz function in Bar module"""
	__tablename__ = "foo_bar_baz"

	id = Column(Integer, primary_key=True)
	guild_id = Column(BigInteger)
	user_id = Column(BigInteger)
	text = Column(String)

	@staticmethod
	def add(guild_id: int, user_id: int, text: str):
		query = FooBarBaz(guild_id=guild_id, user_id=user_id, text=text)
		session.merge(query)
		session.commit()
		return query

	@staticmethod
	def get(guild_id: int, user_id: int):
		query = session.query(FooBarBaz).filter_by(guild_id=guild_id, user_id=user_id).one_or_none()
		return query

	def __repr__(self):
		return (
			f'<FooBarBaz id="{self.id}" '
			f'guild_id="{self.guild_id}" user_id="{self.user_id}" '
			f'text="{self.text}">'
		)
```

---

As pumpkin.py grows, this repository will be updated with Github Actions and other examples.

[pumpkin.py]: https://github.com/Pumpkin-py/pumpkin.py
