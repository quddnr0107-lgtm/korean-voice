"""Fixed scope of one independent source review and one isolated reading repair."""
from batch3.config import ENGINES
from supply38 import corrected_plan as supply

NEW = 'DR-059'
VARIANT = 'supply-word-boundary'
KEY = 'DR-038-SUPPLY-BOUNDARY'
COMMIT = 'eec218e557da3ecc4a2470f8b02d81b4bc205c20'
SCRIPT = 'production303/records/sources/DR-059/본훈_2-15.txt'
SOURCE = '5904f1fe72419f6009b2670d0918c321742da683e1ec89b632ab28eb2bfefcfb'
ENVELOPE = '067afa2388bfdae8acb28dcf64753ea320a6952bab1faacc056446ee075f53f4'
AUDIO = '2a9937eb6ed049af6e05a2c10bffde2f3c32aeb766ef185df1886003edb80ca5'
MEDIA = (753573465, 'bb2f35f2e255abcdc3a08b175d389abb5d06e5bf91482eefa26af6af5dc017d4',
         'c9cb3dcfabb360af5861983aeccbd4bb7a611f23babc26a52daec2c07e3d90a9',60937821,60983784,95)
REQUEST = dict(schema='exam80-private-source-and-reading-repair/v1',lessons=[supply.LESSON,NEW],
               engine_commits={supply.LESSON:ENGINES['DR-045'],NEW:ENGINES['DR-076']},
               private_source_commits={supply.LESSON:supply.SOURCE_COMMIT,NEW:COMMIT},
               source_sha256={supply.LESSON:supply.PINS[supply.LESSON][1],NEW:SOURCE},
               variants={supply.LESSON:VARIANT,NEW:'canonical'})
