export type FlightLocationKind = 'city' | 'airport'

export interface FlightLocation {
  code: string
  city: string
  cityEnglish: string
  name: string
  nameEnglish: string
  country: string
  kind: FlightLocationKind
  aliases: string[]
}

export interface AirportReference extends FlightLocation {
  kind: 'airport'
}

const city = (
  code: string,
  cityName: string,
  cityEnglish: string,
  airportSummary: string,
  aliases: string[] = [],
): FlightLocation => ({
  code,
  city: cityName,
  cityEnglish,
  name: `${cityName}（${airportSummary}）`,
  nameEnglish: `${cityEnglish} metropolitan airports`,
  country: cityName === '东京' || cityName === '大阪' ? '日本' : cityName === '首尔' ? '韩国' : '中国',
  kind: 'city',
  aliases,
})

const airport = (
  code: string,
  cityName: string,
  cityEnglish: string,
  airportName: string,
  airportEnglish: string,
  country: string,
  aliases: string[] = [],
): AirportReference => ({
  code,
  city: cityName,
  cityEnglish,
  name: airportName,
  nameEnglish: airportEnglish,
  country,
  kind: 'airport',
  aliases,
})

export const CITY_LOCATIONS: FlightLocation[] = [
  city('SHA', '上海', 'Shanghai', '虹桥 / 浦东', ['shanghai', '上海市', '虹桥', '浦东']),
  city('BJS', '北京', 'Beijing', '首都 / 大兴', ['beijing', 'peking', '首都', '大兴']),
  city('CTU', '成都', 'Chengdu', '双流 / 天府', ['chengdu', '双流', '天府']),
  city('TYO', '东京', 'Tokyo', '羽田 / 成田', ['tokyo', 'とうきょう', '羽田', '成田']),
  city('OSA', '大阪', 'Osaka', '关西 / 伊丹 / 神户', ['osaka', 'おおさか', '关西', '伊丹', '神户']),
  city('SEL', '首尔', 'Seoul', '仁川 / 金浦', ['seoul', '서울', '仁川', '金浦']),
]

export const AIRPORTS: AirportReference[] = [
  airport('SHA', '上海', 'Shanghai', '虹桥国际机场', 'Shanghai Hongqiao International Airport', '中国', ['hongqiao', '虹桥机场']),
  airport('PVG', '上海', 'Shanghai', '浦东国际机场', 'Shanghai Pudong International Airport', '中国', ['pudong', '浦东机场']),
  airport('PEK', '北京', 'Beijing', '首都国际机场', 'Beijing Capital International Airport', '中国', ['capital', '首都机场']),
  airport('PKX', '北京', 'Beijing', '大兴国际机场', 'Beijing Daxing International Airport', '中国', ['daxing', '大兴机场']),
  airport('CAN', '广州', 'Guangzhou', '白云国际机场', 'Guangzhou Baiyun International Airport', '中国', ['guangzhou', '广州', 'baiyun', '白云']),
  airport('SZX', '深圳', 'Shenzhen', '宝安国际机场', "Shenzhen Bao'an International Airport", '中国', ['shenzhen', '深圳', 'baoan', '宝安']),
  airport('CTU', '成都', 'Chengdu', '双流国际机场', 'Chengdu Shuangliu International Airport', '中国', ['chengdu', '成都', 'shuangliu', '双流']),
  airport('TFU', '成都', 'Chengdu', '天府国际机场', 'Chengdu Tianfu International Airport', '中国', ['chengdu', '成都', 'tianfu', '天府']),
  airport('CKG', '重庆', 'Chongqing', '江北国际机场', 'Chongqing Jiangbei International Airport', '中国', ['chongqing', '重庆', 'jiangbei', '江北']),
  airport('HGH', '杭州', 'Hangzhou', '萧山国际机场', 'Hangzhou Xiaoshan International Airport', '中国', ['hangzhou', '杭州', 'xiaoshan', '萧山']),
  airport('NKG', '南京', 'Nanjing', '禄口国际机场', 'Nanjing Lukou International Airport', '中国', ['nanjing', '南京', 'lukou', '禄口']),
  airport('WUH', '武汉', 'Wuhan', '天河国际机场', 'Wuhan Tianhe International Airport', '中国', ['wuhan', '武汉', 'tianhe', '天河']),
  airport('XIY', '西安', "Xi'an", '咸阳国际机场', "Xi'an Xianyang International Airport", '中国', ['xian', '西安', 'xianyang', '咸阳']),
  airport('TAO', '青岛', 'Qingdao', '胶东国际机场', 'Qingdao Jiaodong International Airport', '中国', ['qingdao', '青岛', 'jiaodong', '胶东']),
  airport('TSN', '天津', 'Tianjin', '滨海国际机场', 'Tianjin Binhai International Airport', '中国', ['tianjin', '天津', 'binhai', '滨海']),
  airport('XMN', '厦门', 'Xiamen', '高崎国际机场', 'Xiamen Gaoqi International Airport', '中国', ['xiamen', '厦门', 'gaoqi', '高崎']),
  airport('FOC', '福州', 'Fuzhou', '长乐国际机场', 'Fuzhou Changle International Airport', '中国', ['fuzhou', '福州', 'changle', '长乐']),
  airport('KMG', '昆明', 'Kunming', '长水国际机场', 'Kunming Changshui International Airport', '中国', ['kunming', '昆明', 'changshui', '长水']),
  airport('CSX', '长沙', 'Changsha', '黄花国际机场', 'Changsha Huanghua International Airport', '中国', ['changsha', '长沙', 'huanghua', '黄花']),
  airport('CGO', '郑州', 'Zhengzhou', '新郑国际机场', 'Zhengzhou Xinzheng International Airport', '中国', ['zhengzhou', '郑州', 'xinzheng', '新郑']),
  airport('SHE', '沈阳', 'Shenyang', '桃仙国际机场', 'Shenyang Taoxian International Airport', '中国', ['shenyang', '沈阳', 'taoxian', '桃仙']),
  airport('DLC', '大连', 'Dalian', '周水子国际机场', 'Dalian Zhoushuizi International Airport', '中国', ['dalian', '大连', 'zhoushuizi', '周水子']),
  airport('HRB', '哈尔滨', 'Harbin', '太平国际机场', 'Harbin Taiping International Airport', '中国', ['harbin', '哈尔滨', 'taiping', '太平']),
  airport('CGQ', '长春', 'Changchun', '龙嘉国际机场', 'Changchun Longjia International Airport', '中国', ['changchun', '长春', 'longjia', '龙嘉']),
  airport('NGB', '宁波', 'Ningbo', '栎社国际机场', 'Ningbo Lishe International Airport', '中国', ['ningbo', '宁波', 'lishe', '栎社']),
  airport('WNZ', '温州', 'Wenzhou', '龙湾国际机场', 'Wenzhou Longwan International Airport', '中国', ['wenzhou', '温州', 'longwan', '龙湾']),
  airport('TNA', '济南', 'Jinan', '遥墙国际机场', 'Jinan Yaoqiang International Airport', '中国', ['jinan', '济南', 'yaoqiang', '遥墙']),
  airport('HFE', '合肥', 'Hefei', '新桥国际机场', 'Hefei Xinqiao International Airport', '中国', ['hefei', '合肥', 'xinqiao', '新桥']),
  airport('KHN', '南昌', 'Nanchang', '昌北国际机场', 'Nanchang Changbei International Airport', '中国', ['nanchang', '南昌', 'changbei', '昌北']),
  airport('KWE', '贵阳', 'Guiyang', '龙洞堡国际机场', 'Guiyang Longdongbao International Airport', '中国', ['guiyang', '贵阳', 'longdongbao', '龙洞堡']),
  airport('NNG', '南宁', 'Nanning', '吴圩国际机场', 'Nanning Wuxu International Airport', '中国', ['nanning', '南宁', 'wuxu', '吴圩']),
  airport('HAK', '海口', 'Haikou', '美兰国际机场', 'Haikou Meilan International Airport', '中国', ['haikou', '海口', 'meilan', '美兰']),
  airport('SYX', '三亚', 'Sanya', '凤凰国际机场', 'Sanya Phoenix International Airport', '中国', ['sanya', '三亚', 'phoenix', '凤凰']),
  airport('URC', '乌鲁木齐', 'Urumqi', '天山国际机场', 'Urumqi Tianshan International Airport', '中国', ['urumqi', '乌鲁木齐', 'tianshan', '天山']),
  airport('HKG', '香港', 'Hong Kong', '香港国际机场', 'Hong Kong International Airport', '中国香港', ['hongkong', 'hong kong', '香港', 'chek lap kok', '赤鱲角']),
  airport('MFM', '澳门', 'Macao', '澳门国际机场', 'Macau International Airport', '中国澳门', ['macao', 'macau', '澳门']),
  airport('TPE', '台北', 'Taipei', '桃园国际机场', 'Taiwan Taoyuan International Airport', '中国台湾', ['taipei', '台北', 'taoyuan', '桃园']),
  airport('TSA', '台北', 'Taipei', '松山机场', 'Taipei Songshan Airport', '中国台湾', ['taipei', '台北', 'songshan', '松山']),
  airport('NRT', '东京', 'Tokyo', '成田国际机场', 'Narita International Airport', '日本', ['tokyo', '东京', 'narita', '成田', 'とうきょう']),
  airport('HND', '东京', 'Tokyo', '羽田机场', 'Tokyo Haneda Airport', '日本', ['tokyo', '东京', 'haneda', '羽田', 'とうきょう']),
  airport('KIX', '大阪', 'Osaka', '关西国际机场', 'Kansai International Airport', '日本', ['osaka', '大阪', 'kansai', '关西', 'おおさか']),
  airport('ITM', '大阪', 'Osaka', '伊丹机场', 'Osaka International Airport (Itami)', '日本', ['osaka', '大阪', 'itami', '伊丹']),
  airport('UKB', '神户', 'Kobe', '神户机场', 'Kobe Airport', '日本', ['kobe', '神户', 'こうべ']),
  airport('NGO', '名古屋', 'Nagoya', '中部国际机场', 'Chubu Centrair International Airport', '日本', ['nagoya', '名古屋', 'chubu', 'centrair', '中部']),
  airport('FUK', '福冈', 'Fukuoka', '福冈机场', 'Fukuoka Airport', '日本', ['fukuoka', '福冈', 'ふくおか']),
  airport('CTS', '札幌', 'Sapporo', '新千岁机场', 'New Chitose Airport', '日本', ['sapporo', '札幌', 'chitose', '新千岁']),
  airport('OKA', '冲绳', 'Okinawa', '那霸机场', 'Naha Airport', '日本', ['okinawa', '冲绳', 'naha', '那霸']),
  airport('SDJ', '仙台', 'Sendai', '仙台机场', 'Sendai Airport', '日本', ['sendai', '仙台']),
  airport('HIJ', '广岛', 'Hiroshima', '广岛机场', 'Hiroshima Airport', '日本', ['hiroshima', '广岛']),
  airport('OKJ', '冈山', 'Okayama', '冈山机场', 'Okayama Airport', '日本', ['okayama', '冈山']),
  airport('TAK', '高松', 'Takamatsu', '高松机场', 'Takamatsu Airport', '日本', ['takamatsu', '高松']),
  airport('MYJ', '松山', 'Matsuyama', '松山机场', 'Matsuyama Airport', '日本', ['matsuyama', '松山']),
  airport('KOJ', '鹿儿岛', 'Kagoshima', '鹿儿岛机场', 'Kagoshima Airport', '日本', ['kagoshima', '鹿儿岛']),
  airport('KMJ', '熊本', 'Kumamoto', '熊本机场', 'Kumamoto Airport', '日本', ['kumamoto', '熊本']),
  airport('NGS', '长崎', 'Nagasaki', '长崎机场', 'Nagasaki Airport', '日本', ['nagasaki', '长崎']),
  airport('OIT', '大分', 'Oita', '大分机场', 'Oita Airport', '日本', ['oita', '大分']),
  airport('KMQ', '金泽', 'Kanazawa', '小松机场', 'Komatsu Airport', '日本', ['kanazawa', '金泽', 'komatsu', '小松']),
  airport('KIJ', '新潟', 'Niigata', '新潟机场', 'Niigata Airport', '日本', ['niigata', '新潟']),
  airport('FSZ', '静冈', 'Shizuoka', '富士山静冈机场', 'Mt. Fuji Shizuoka Airport', '日本', ['shizuoka', '静冈', 'fuji', '富士山']),
  airport('TOY', '富山', 'Toyama', '富山机场', 'Toyama Airport', '日本', ['toyama', '富山']),
  airport('AOJ', '青森', 'Aomori', '青森机场', 'Aomori Airport', '日本', ['aomori', '青森']),
  airport('HKD', '函馆', 'Hakodate', '函馆机场', 'Hakodate Airport', '日本', ['hakodate', '函馆']),
  airport('IBR', '茨城', 'Ibaraki', '茨城机场', 'Ibaraki Airport', '日本', ['ibaraki', '茨城']),
  airport('ISG', '石垣岛', 'Ishigaki', '新石垣机场', 'New Ishigaki Airport', '日本', ['ishigaki', '石垣']),
  airport('MMY', '宫古岛', 'Miyakojima', '宫古机场', 'Miyako Airport', '日本', ['miyakojima', 'miyako', '宫古岛']),
  airport('ICN', '首尔', 'Seoul', '仁川国际机场', 'Incheon International Airport', '韩国', ['seoul', '首尔', 'incheon', '仁川', '서울']),
  airport('GMP', '首尔', 'Seoul', '金浦国际机场', 'Gimpo International Airport', '韩国', ['seoul', '首尔', 'gimpo', '金浦', '서울']),
  airport('SIN', '新加坡', 'Singapore', '樟宜机场', 'Singapore Changi Airport', '新加坡', ['singapore', '新加坡', 'changi', '樟宜']),
  airport('BKK', '曼谷', 'Bangkok', '素万那普机场', 'Suvarnabhumi Airport', '泰国', ['bangkok', '曼谷', 'suvarnabhumi', '素万那普']),
  airport('KUL', '吉隆坡', 'Kuala Lumpur', '吉隆坡国际机场', 'Kuala Lumpur International Airport', '马来西亚', ['kuala lumpur', '吉隆坡']),
]

const LOCATION_BY_CODE = new Map<string, FlightLocation>()
for (const location of [...CITY_LOCATIONS, ...AIRPORTS]) {
  if (!LOCATION_BY_CODE.has(location.code)) LOCATION_BY_CODE.set(location.code, location)
}

export const FLIGHT_LOCATIONS = [...LOCATION_BY_CODE.values()]

export function findFlightLocation(code: string | null | undefined): FlightLocation | undefined {
  return code ? LOCATION_BY_CODE.get(code.trim().toUpperCase()) : undefined
}

export function readableLocation(code: string): string {
  const location = findFlightLocation(code)
  if (!location) return code.toUpperCase()
  return location.kind === 'city'
    ? `${location.name} · ${location.code}`
    : `${location.city} · ${location.name}（${location.code}）`
}

export function locationSearchTerms(location: FlightLocation): string[] {
  return [
    location.code,
    location.city,
    location.cityEnglish,
    location.name,
    location.nameEnglish,
    location.country,
    ...location.aliases,
  ]
}
