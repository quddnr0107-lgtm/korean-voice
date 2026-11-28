"""Source-bound settings for reviewed private form corrections only."""
SCHEMA = 'exam80-directive-private-corrections/v2'
ENGINES = {
    'DR-045': 'db2081bf68cdc3e4cd6a73a3a719b68f58cb675f',
    'DR-076': '49637dfd44f2890a276308839757a573a92087a9',
}
COMMITS = {
    'DR-045': 'a5d1247b02bb13c656bf885d24acd8059214de43',
    'DR-076': '71649ea8dfbec359a194ea249d60e9efdc253a59',
}
SOURCE_FILES = {
    'DR-045': ('698c30591e904369028ffcf97e64990a6cd29c09e0e010b0ec3a802898ef020e',
               '7008d178fd273f60020cf77fcd9383bbba2983c06e208417ef916330f32e36bc'),
    'DR-076': ('225cf84e12be8b639c103ed1de8373e750a8fff25a4ceb3008eb839287f35176',
               '7f08186eba16d22cd3b18780e0938acb28737dc6267566e6911e5ad9065149c3'),
}
PATHS = {
    'DR-045': 'production303/records/sources/DR-045/본훈_2-2의2.txt',
    'DR-076': 'production303/records/sources/DR-076/본훈_2-32.txt',
}
MEDIA = {
    'DR-045': (753494161, 'fe43e2da031ff07a72e98f5633a30ccc25f3ada249d78f7f4ac4606df1a7d1d2',
               'eaf28c01c9caaa98218f961edba76436d6ff556cb360b37b868698eef8965a18',
               34285104, 34310376, 47),
    'DR-076': (753570047, 'babe5cea98f5c482c57bec2a9f617b4f295404bccce12cf3b0301b9160cbe99f',
               '16253b8ca9adec816f0029e6d9db649dc68dfc19fe6fcf31efeb25d13a0fe239',
               67375539, 67425525, 104),
}


def request():
    return dict(schema=SCHEMA, lessons=list(COMMITS), private_source_commits=COMMITS, engine_commits=ENGINES)
