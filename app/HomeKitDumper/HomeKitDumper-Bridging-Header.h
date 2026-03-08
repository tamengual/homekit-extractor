#import <Foundation/Foundation.h>

@interface SafeKVCReader : NSObject
+ (nullable id)valueForKey:(NSString *)key onObject:(NSObject *)object;
@end
