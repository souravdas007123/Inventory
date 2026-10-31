"""Lists used on invoices: the Indian states with their GST state codes, and the countries a customer can pick."""

INDIA_STATES = {
    '01': 'Jammu and Kashmir', '02': 'Himachal Pradesh', '03': 'Punjab', '04': 'Chandigarh', '05': 'Uttarakhand',
    '06': 'Haryana', '07': 'Delhi', '08': 'Rajasthan', '09': 'Uttar Pradesh', '10': 'Bihar', '11': 'Sikkim',
    '12': 'Arunachal Pradesh', '13': 'Nagaland', '14': 'Manipur', '15': 'Mizoram', '16': 'Tripura', '17': 'Meghalaya',
    '18': 'Assam', '19': 'West Bengal', '20': 'Jharkhand', '21': 'Odisha', '22': 'Chhattisgarh', '23': 'Madhya Pradesh',
    '24': 'Gujarat', '26': 'Dadra and Nagar Haveli and Daman and Diu', '27': 'Maharashtra', '29': 'Karnataka', '30': 'Goa',
    '31': 'Lakshadweep', '32': 'Kerala', '33': 'Tamil Nadu', '34': 'Puducherry', '35': 'Andaman and Nicobar Islands',
    '36': 'Telangana', '37': 'Andhra Pradesh', '38': 'Ladakh',
}
INDIA_STATES['25'] = INDIA_STATES['26']              # the old code of Daman and Diu: still seen on older GST numbers

COUNTRIES = {
    'IN': 'India', 'US': 'United States', 'GB': 'United Kingdom', 'AE': 'United Arab Emirates', 'SA': 'Saudi Arabia',
    'QA': 'Qatar', 'KW': 'Kuwait', 'OM': 'Oman', 'BH': 'Bahrain', 'SG': 'Singapore', 'MY': 'Malaysia', 'ID': 'Indonesia',
    'TH': 'Thailand', 'VN': 'Vietnam', 'PH': 'Philippines', 'LK': 'Sri Lanka', 'NP': 'Nepal', 'BD': 'Bangladesh',
    'BT': 'Bhutan', 'MV': 'Maldives', 'PK': 'Pakistan', 'AF': 'Afghanistan', 'MM': 'Myanmar', 'KH': 'Cambodia',
    'CN': 'China', 'HK': 'Hong Kong', 'JP': 'Japan', 'KR': 'South Korea', 'TW': 'Taiwan', 'AU': 'Australia',
    'NZ': 'New Zealand', 'CA': 'Canada', 'MX': 'Mexico', 'BR': 'Brazil', 'AR': 'Argentina', 'CL': 'Chile', 'CO': 'Colombia',
    'PE': 'Peru', 'ZA': 'South Africa', 'NG': 'Nigeria', 'KE': 'Kenya', 'GH': 'Ghana', 'EG': 'Egypt', 'MA': 'Morocco',
    'ET': 'Ethiopia', 'TZ': 'Tanzania', 'UG': 'Uganda', 'MU': 'Mauritius', 'DE': 'Germany', 'FR': 'France', 'IT': 'Italy',
    'ES': 'Spain', 'PT': 'Portugal', 'NL': 'Netherlands', 'BE': 'Belgium', 'LU': 'Luxembourg', 'IE': 'Ireland',
    'AT': 'Austria', 'CH': 'Switzerland', 'SE': 'Sweden', 'NO': 'Norway', 'DK': 'Denmark', 'FI': 'Finland', 'IS': 'Iceland',
    'PL': 'Poland', 'CZ': 'Czechia', 'SK': 'Slovakia', 'HU': 'Hungary', 'RO': 'Romania', 'BG': 'Bulgaria', 'GR': 'Greece',
    'HR': 'Croatia', 'SI': 'Slovenia', 'EE': 'Estonia', 'LV': 'Latvia', 'LT': 'Lithuania', 'CY': 'Cyprus', 'MT': 'Malta',
    'RU': 'Russia', 'UA': 'Ukraine', 'TR': 'Turkey', 'IL': 'Israel', 'JO': 'Jordan', 'LB': 'Lebanon', 'IQ': 'Iraq',
    'IR': 'Iran', 'KZ': 'Kazakhstan', 'UZ': 'Uzbekistan', 'ZZ': 'Other country',
}
