from .labels import Labels
from .characters.Aemeath import Aemeath
from .characters.Aalto import Aalto
from .characters.Augusta import Augusta
from .characters.Baizhi import Baizhi
from .characters.BaseChar import BaseChar, CharType, Elements, get_default_buff_time
from .characters.Brant import Brant
from .characters.Calcharo import Calcharo
from .characters.Camellya import Camellya
from .characters.Cantarella import Cantarella
from .characters.Carlotta import Carlotta
from .characters.Cartethyia import Cartethyia
from .characters.Changli import Changli
from .characters.Chisa import Chisa
from .characters.Chixia import Chixia
from .characters.Ciaccona import Ciaccona
from .characters.Danjin import Danjin
from .characters.Denia import Denia
from .characters.Douling import Douling
from .characters.Encore import Encore
from .characters.Galbrena import Galbrena
from .characters.Rover import Rover
from .characters.Hiyuki import Hiyuki
from .characters.Hsin import Hsin
from .characters.Iuno import Iuno
from .characters.Jianxin import Jianxin
from .characters.Jinhsi import Jinhsi
from .characters.JingRan import JingRan
from .characters.Jiyan import Jiyan
from .characters.Lingyang import Lingyang
from .characters.Linnai import Linnai
from .characters.Lucilla import Lucilla
from .characters.Lucy import Lucy
from .characters.Lumi import Lumi
from .characters.Luhesi import Luhesi
from .characters.Lupa import Lupa
from .characters.Mortefi import Mortefi
from .characters.Mornye import Mornye
from .characters.Phoebe import Phoebe
from .characters.Phrolova import Phrolova
from .characters.Qingxiao import Qingxiao
from .characters.Qiuyuan import Qiuyuan
from .characters.Rebecca import Rebecca
from .characters.Roccia import Roccia
from .characters.Sanhua import Sanhua
from .characters.ShoreKeeper import ShoreKeeper
from .characters.Suisui import Suisui
from .characters.Suoming import Suoming
from .characters.Taoqi import Taoqi
from .characters.Verina import Verina
from .characters.Xiangliyao import Xiangliyao
from .characters.Xigelika import Xigelika
from .characters.Yinlin import Yinlin
from .characters.Yangyang import Yangyang
from .characters.YangYangSp import YangYangSp
from .characters.Youhu import Youhu
from .characters.Yuanwu import Yuanwu
from .characters.Zani import Zani
from .characters.Zhezhi import Zhezhi

_char_dict_raw = {
    Labels.char_aalto: {'cls': Aalto, 'char_type': CharType.SUB_DPS,
                             'ring_index': Elements.WIND},
    Labels.char_yangyang: {'cls': Yangyang, 'char_type': CharType.SUB_DPS, 'ring_index': Elements.WIND},
    Labels.yangyang_sp: {'cls': YangYangSp, 'char_type': CharType.MAIN_DPS,
                         'ring_index': Elements.HAVOC},
    Labels.char_yinlin: {'cls': Yinlin, 'char_type': CharType.SUB_DPS,
                         'ring_index': Elements.ELECTRIC},
    Labels.char_verina: {'cls': Verina, 'char_type': CharType.HEALER,
                         'ring_index': Elements.SPECTRO},
    Labels.char_shorekeeper: {'cls': ShoreKeeper, 'char_type': CharType.HEALER,
                              'ring_index': Elements.SPECTRO},
    Labels.char_suisui: {'cls': Suisui, 'char_type': CharType.HEALER},
    Labels.char_taoqi: {'cls': Taoqi, 'char_type': CharType.HEALER,
                        'ring_index': Elements.HAVOC},
    (Labels.char_rover, Labels.char_rover_male): {'cls': Rover, 'char_type': CharType.MAIN_DPS},
    Labels.char_encore: {'cls': Encore, 'char_type': CharType.MAIN_DPS,
                         'ring_index': Elements.FIRE},
    Labels.char_jianxin: {'cls': Jianxin, 'char_type': CharType.HEALER,
                          'ring_index': Elements.WIND},
    (Labels.char_sanhua, Labels.char_sanhua2): {'cls': Sanhua, 'char_type': CharType.SUB_DPS,
                                                'ring_index': Elements.ICE},
    (Labels.char_jinhsi, Labels.char_jinhsi2): {'cls': Jinhsi, 'char_type': CharType.MAIN_DPS,
                                                'ring_index': Elements.SPECTRO},
    Labels.char_yuanwu: {'cls': Yuanwu, 'char_type': CharType.SUB_DPS,
                         'ring_index': Elements.ELECTRIC},
    (Labels.chang_changli, Labels.char_changli2): {'cls': Changli, 'char_type': CharType.MAIN_DPS,
                                                   'ring_index': Elements.FIRE},
    Labels.char_chixia: {'cls': Chixia, 'char_type': CharType.MAIN_DPS,
                         'ring_index': Elements.FIRE},
    Labels.char_danjin: {'cls': Danjin, 'char_type': CharType.SUB_DPS,
                         'ring_index': Elements.HAVOC},
    Labels.char_baizhi: {'cls': Baizhi, 'char_type': CharType.HEALER,
                         'ring_index': Elements.ICE},
    Labels.char_calcharo: {'cls': Calcharo, 'char_type': CharType.MAIN_DPS,
                           'ring_index': Elements.ELECTRIC},
    Labels.char_jiyan: {'cls': Jiyan, 'char_type': CharType.MAIN_DPS,
                        'ring_index': Elements.WIND},
    Labels.char_lingyang: {'cls': Lingyang, 'char_type': CharType.MAIN_DPS,
                                'ring_index': Elements.ICE},
    Labels.char_mortefi: {'cls': Mortefi, 'char_type': CharType.SUB_DPS,
                          'ring_index': Elements.FIRE},
    Labels.char_zhezhi: {'cls': Zhezhi, 'char_type': CharType.SUB_DPS,
                         'ring_index': Elements.ICE},
    Labels.char_xiangliyao: {'cls': Xiangliyao, 'char_type': CharType.MAIN_DPS,
                             'ring_index': Elements.ELECTRIC},
    Labels.char_camellya: {'cls': Camellya, 'char_type': CharType.MAIN_DPS,
                           'ring_index': Elements.HAVOC},
    Labels.char_youhu: {'cls': Youhu, 'char_type': CharType.HEALER,
                        'ring_index': Elements.ICE},
    (Labels.char_carlotta, Labels.char_carlotta2): {'cls': Carlotta, 'char_type': CharType.MAIN_DPS,
                                                    'ring_index': Elements.ICE},
    Labels.char_roccia: {'cls': Roccia, 'char_type': CharType.SUB_DPS, 'ring_index': Elements.HAVOC},
    Labels.char_phoebe: {'cls': Phoebe, 'char_type': CharType.SUB_DPS, 'ring_index': Elements.SPECTRO},
    Labels.char_brant: {'cls': Brant, 'char_type': CharType.HEALER, 'ring_index': Elements.FIRE},
    Labels.char_cantarella: {'cls': Cantarella, 'char_type': CharType.HEALER, 'ring_index': Elements.HAVOC},
    (Labels.char_zani, Labels.char_zani2): {'cls': Zani, 'char_type': CharType.MAIN_DPS,
                                            'ring_index': Elements.SPECTRO},
    Labels.char_ciaccona: {'cls': Ciaccona, 'char_type': CharType.SUB_DPS, 'ring_index': Elements.WIND},
    Labels.char_cartethyia: {'cls': Cartethyia, 'char_type': CharType.MAIN_DPS, 'ring_index': Elements.WIND},
    Labels.char_lupa: {'cls': Lupa, 'char_type': CharType.SUB_DPS, 'ring_index': Elements.FIRE},
    Labels.char_phrolova: {'cls': Phrolova, 'char_type': CharType.MAIN_DPS, 'ring_index': Elements.HAVOC},
    Labels.Augusta: {'cls': Augusta, 'char_type': CharType.MAIN_DPS, 'ring_index': Elements.ELECTRIC},
    Labels.char_iuno: {'cls': Iuno, 'char_type': CharType.SUB_DPS,
                       'ring_index': Elements.WIND},
    Labels.char_galbrena: {'cls': Galbrena, 'char_type': CharType.MAIN_DPS, 'ring_index': Elements.FIRE},
    Labels.char_chouyuan: {'cls': Qiuyuan, 'char_type': CharType.SUB_DPS, 'ring_index': Elements.WIND},
    (Labels.char_chisa, Labels.char_chisa2): {'cls': Chisa, 'char_type': CharType.HEALER, 'buff_time': 20,
                                              'ring_index': Elements.HAVOC},
    Labels.char_denia: {'cls': Denia, 'char_type': CharType.SUB_DPS, 'buff_time': 14, 'ring_index': Elements.FIRE},
    Labels.char_douling: {'cls': Douling, 'char_type': CharType.HEALER, 'ring_index': Elements.ELECTRIC},
    (Labels.char_linnai, Labels.char_linnai2): {'cls': Linnai, 'char_type': CharType.SUB_DPS,
                                                'ring_index': Elements.SPECTRO},
    (Labels.char_moning, Labels.char_moning_new): {'cls': Mornye, 'char_type': CharType.HEALER,
                                                   'ring_index': Elements.FIRE},
    Labels.char_aemeath: {'cls': Aemeath, 'char_type': CharType.MAIN_DPS, 'ring_index': Elements.FIRE},
    Labels.char_xigelika: {'cls': Xigelika, 'char_type': CharType.MAIN_DPS, 'ring_index': Elements.WIND},
    Labels.char_luhesi: {'cls': Luhesi, 'char_type': CharType.MAIN_DPS, 'ring_index': Elements.SPECTRO},
    Labels.char_hiyuki: {'cls': Hiyuki, 'char_type': CharType.MAIN_DPS, 'ring_index': Elements.ICE},
    Labels.char_lucilla: {'cls': Lucilla, 'char_type': CharType.SUB_DPS, 'ring_index': Elements.ICE,
                          'target_box_short_combat_check': True},
    Labels.char_lucy: {'cls': Lucy, 'char_type': CharType.MAIN_DPS, 'ring_index': Elements.SPECTRO},
    Labels.char_lumi: {'cls': Lumi, 'char_type': CharType.SUB_DPS, 'ring_index': Elements.ELECTRIC},
    Labels.char_rebecca: {'cls': Rebecca, 'char_type': CharType.SUB_DPS, 'ring_index': Elements.ELECTRIC},
    Labels.char_qingxiao: {'cls': Qingxiao, 'char_type': CharType.MAIN_DPS, 'ring_index': Elements.WIND},
    Labels.char_jingran: {'cls': JingRan, 'char_type': CharType.MAIN_DPS, 'ring_index': Elements.FIRE},
    Labels.char_hsin: {'cls': Hsin, 'char_type': CharType.MAIN_DPS, 'ring_index': Elements.ELECTRIC},
    Labels.char_suoming: {'cls': Suoming, 'char_type': CharType.SUB_DPS, 'buff_time': 8,
                         'ring_index': Elements.ELECTRIC},
}

char_dict = {}
for keys, value in _char_dict_raw.items():
    value = dict(value)
    value.setdefault('buff_time', get_default_buff_time(value.get('char_type', CharType.MAIN_DPS)))
    template_names = keys if isinstance(keys, tuple) else (keys,)
    value['canonical_name'] = template_names[0]
    value['template_names'] = template_names
    for key in template_names:
        char_dict[key] = value

char_names = char_dict.keys()
